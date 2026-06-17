/**
 * @file       sys_sensor_fusion.c
 * @copyright  [Your Copyright]
 * @license    [Your License]
 * @version    1.0.0
 * @date       25/03/26
 * @author     Dong Son
 *
 * @brief
 */
/* Includes ----------------------------------------------------------- */
#include "config.h"
#include "sys_sensor_fusion.h"
#include "bsp_imu.h"
#include "bsp_io.h"
#include "bsp_util.h"
#include "err.h"
#include "mw_filter.h"
#include "network/network_cmd.h"
#include <stddef.h>
#include <math.h>
#include <string.h>
#include <stdio.h>
#include "positioning_config.h"
#include "sys_config.h"
#include "sys_logger.h"
#include "cmsis_os2.h"

/* Private defines ---------------------------------------------------- */
#define NUM_STATE     			8
#define NUM_PREDICT_NOISE     	3
#define NUM_UPDATE_NOISE     	3
#define N       				(NUM_STATE + NUM_PREDICT_NOISE)
#define M						(NUM_STATE + NUM_UPDATE_NOISE)
#define NUM_PREDICT_SIGMA  		(2*N + 1)
#define NUM_UPDATE_SIGMA  		(2*M + 1)

#define UKF_ALPHA               SYS_FUSION_UKF_ALPHA
#define UKF_KAPPA               SYS_FUSION_UKF_KAPPA
#define UKF_BETA                SYS_FUSION_UKF_BETA
#define N_PLUS_LAMBDA_N         (UKF_ALPHA * UKF_ALPHA * (N + UKF_KAPPA))
#define UKF_LAMBDA_N  			(N_PLUS_LAMBDA_N - N)
#define GAMMA_N       			sqrtf(N_PLUS_LAMBDA_N)
#define M_PLUS_LAMBDA_M         (UKF_ALPHA * UKF_ALPHA * (M + UKF_KAPPA))
#define UKF_LAMBDA_M  			(M_PLUS_LAMBDA_M - M)
#define GAMMA_M       			sqrtf(M_PLUS_LAMBDA_M)

#define Qa						SYS_FUSION_UKF_QA
#define Qg						SYS_FUSION_UKF_QG
#define R_uwb					SYS_FUSION_UKF_R_UWB

#define SYS_SENSOR_FUSION_PI    3.14159265358979323846f
#define SYS_SENSOR_FUSION_2PI   (2.0f * SYS_SENSOR_FUSION_PI)
#define RAD2DEG							180.0f / 3.14159265358979323846f

/* Private enumerate/structure ---------------------------------------- */
typedef struct
{
	sys_sensor_fusion_data_t state;

    float32_t P_data[NUM_STATE * NUM_STATE]; 					// P [8x8]
    float32_t Q_data[NUM_PREDICT_NOISE * NUM_PREDICT_NOISE]; 	// Q [3x3]
    float32_t R_data[NUM_UPDATE_NOISE * NUM_UPDATE_NOISE]; 		// R [3x3]

    // Weights
    float32_t Wm_N[NUM_PREDICT_SIGMA];
	float32_t Wc_N[NUM_PREDICT_SIGMA];
	float32_t Wm_M[NUM_UPDATE_SIGMA];
	float32_t Wc_M[NUM_UPDATE_SIGMA];

	bool is_first_frame;
	float32_t X_sigma_pred[NUM_STATE][NUM_PREDICT_SIGMA];
	float32_t Noise_sigma_pred[NUM_PREDICT_NOISE][NUM_PREDICT_SIGMA];

    bsp_imu_data_t imu_old;
    bsp_imu_data_t imu_current;

    // Instances của CMSIS-DSP
    arm_matrix_instance_f32 mat_P;
    arm_matrix_instance_f32 mat_Q;
    arm_matrix_instance_f32 mat_R;

	// Flag
	bool enable_predict;
	bool enable_update;
	bool initialized;
	bool has_predict_tick;
	uint32_t last_predict_tick;

} ukf_core_t;

/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
static ukf_core_t ukf = {0};
static uint16_t predict_delta_ms = 0;
static uint16_t update_delta_ms = 0;
unsigned long sys_predict_count = 0;
unsigned long sys_update_count = 0;
unsigned long sys_predict_err_count = 0;
unsigned long sys_update_err_count = 0;
unsigned long sys_update_cholesky_err_count = 0;
unsigned long sys_update_inverse_err_count = 0;

/* Private variables -------------------------------------------------- */
float yaw = 0.0f;
float b_gz_t = 0.0f;
static ukf_init_filter_t s_ukf_init_filter;
static ukf_init_distance_filter_t s_ukf_init_dist_filter;
static float s_latest_distances[NUM_ANCHORS] = {0.0f};
static double s_latest_fp_amp_norm[NUM_ANCHORS] = {0.0};
static double s_latest_fp_snr[NUM_ANCHORS] = {0.0};
static uint32_t s_error_count = 0U;
static uint8_t s_last_selected_anchors_mask = 0U;
static float s_latest_tril_x = 0.0f;
static float s_latest_tril_y = 0.0f;
#if UKF_BLE_STREAM_TEST_ENABLE
static uint32_t s_stream_test_sample_idx = 0U;
#endif

/* Private function prototypes ---------------------------------------- */
static float normalize_angle(float angle);
static float calc_dt(void);
static void reset_runtime_state(void);
static void send_uart_snapshot(void);
#if UKF_BLE_STREAM_TEST_ENABLE
static void configure_adv(network_core_t *stream);
#endif

/* Function definitions ----------------------------------------------- */
sys_sensor_fusion_err_t sys_sensor_fusion_init(sys_sensor_fusion_data_t *p_ukf)
{
	CHECK_ERR((bsp_imu_init() == BSP_IMU_OK || p_ukf != NULL), SYS_SENSOR_FUSION_ERR);

	bsp_imu_bias_t imu_bias;

	CHECK_ERR((bsp_imu_get_bias_data(&imu_bias) == BSP_IMU_OK), SYS_SENSOR_FUSION_ERR);

	ukf.state.b_ax = imu_bias.bias_ax;
	ukf.state.b_ay = imu_bias.bias_ay;
	ukf.state.b_gz = imu_bias.bias_gz;
	b_gz_t = imu_bias.bias_gz;

	// P
    for(int i=0; i<64; i++) ukf.P_data[i] = 0.0f;
	ukf.P_data[0] 	= 0.1f;  	// p_x
	ukf.P_data[9] 	= 0.1f;   	// p_y
	ukf.P_data[18] 	= 0.1f; 	// v_x
	ukf.P_data[27] 	= 0.1f;  	// v_y
	ukf.P_data[36] 	= 1.0e-10f;  	// theta
	ukf.P_data[45] 	= 1.0e-5f; 	// Bias
	ukf.P_data[54] 	= 1.0e-5f;
	ukf.P_data[63] 	= 1.0e-10f;

    // Q
    for(int i=0; i<9; i++) ukf.Q_data[i] = 0.0f;
    ukf.Q_data[0] = Qa;
    ukf.Q_data[4] = Qa;
    ukf.Q_data[8] = Qg;

    // R
    for(int i=0; i<9; i++) ukf.R_data[i] = 0.0f;
    ukf.R_data[0] = R_uwb;
    ukf.R_data[4] = R_uwb;
    ukf.R_data[8] = R_uwb;

    // Sigma
    ukf.Wm_N[0] = UKF_LAMBDA_N / (N + UKF_LAMBDA_N);
	ukf.Wc_N[0] = ukf.Wm_N[0] + (1.0f - UKF_ALPHA * UKF_ALPHA + UKF_BETA);
	float w_rest_n = 1.0f / (2.0f * (N + UKF_LAMBDA_N));
	for(int i=1; i<NUM_PREDICT_SIGMA; i++)
	{
		ukf.Wm_N[i] = w_rest_n;
		ukf.Wc_N[i] = w_rest_n;
	}

	ukf.Wm_M[0] = UKF_LAMBDA_M / (M + UKF_LAMBDA_M);
	ukf.Wc_M[0] = ukf.Wm_M[0] + (1.0f - UKF_ALPHA * UKF_ALPHA + UKF_BETA);
	float w_rest_m = 1.0f / (2.0f * (M + UKF_LAMBDA_M));
	for(int i=1; i<NUM_UPDATE_SIGMA; i++)
	{
		ukf.Wm_M[i] = w_rest_m;
		ukf.Wc_M[i] = w_rest_m;
	}

	// Matrix
    arm_mat_init_f32(&ukf.mat_P, NUM_STATE, NUM_STATE, ukf.P_data);
    arm_mat_init_f32(&ukf.mat_Q, NUM_PREDICT_NOISE, NUM_PREDICT_NOISE, ukf.Q_data);
    arm_mat_init_f32(&ukf.mat_R, NUM_UPDATE_NOISE, NUM_UPDATE_NOISE, ukf.R_data);

    *p_ukf = ukf.state;

    ukf.is_first_frame = true;
    ukf.initialized = false;
    ukf.has_predict_tick = false;
    ukf.last_predict_tick = 0U;
    sys_sensor_fusion_clear_update_flag();
    sys_sensor_fusion_clear_predict_flag();
    reset_runtime_state();

    return SYS_SENSOR_FUSION_OK;
}

sys_sensor_fusion_err_t sys_sensor_fusion_predict(sys_sensor_fusion_data_t *p_ukf)
{
	CHECK_ERR(p_ukf != NULL, SYS_SENSOR_FUSION_ERR);

    uint32_t sys_predict_tick_ms = HAL_GetTick();
    float dt = calc_dt();
	sys_predict_count++;

	bsp_imu_get_raw_data(&ukf.imu_current);

	/* On the first frame imu_old is still zero-initialized; pair it with the
	 * current sample so the trapezoidal integration below does not integrate
	 * against a bogus zero (which corrupts the very first yaw/motion step). */
	if (ukf.is_first_frame)
	{
		ukf.imu_old = ukf.imu_current;
	}

	yaw += (0.5f * (ukf.imu_old.gz + ukf.imu_current.gz) - b_gz_t) * dt;

	if (ukf.is_first_frame)
	{
		static float32_t P_aug[N * N];
		static float32_t L_aug[N * N];
		static arm_matrix_instance_f32 mat_Paug, mat_Laug;
		
		memset(P_aug, 0, sizeof(P_aug));
		memset(L_aug, 0, sizeof(L_aug));
		
		arm_mat_init_f32(&mat_Paug, N, N, P_aug);
		arm_mat_init_f32(&mat_Laug, N, N, L_aug);

        // P_aug 	= [P 0 	0]
		// 			= [0 Qa 0]
		// 			= [0 0 	Qg]
		for(int i=0; i<NUM_STATE; i++)
		{
			for(int j=0; j<NUM_STATE; j++) P_aug[i*N + j] = ukf.P_data[i*NUM_STATE + j];
		}
		for(int i=0; i<NUM_PREDICT_NOISE; i++)
		{
			for(int j=0; j<NUM_PREDICT_NOISE; j++) P_aug[(NUM_STATE+i)*N + (NUM_STATE+j)] = ukf.Q_data[i*NUM_PREDICT_NOISE + j];
		}

        // L_aug = chol(P_aug)
		arm_status status = arm_mat_cholesky_f32(&mat_Paug, &mat_Laug);
		if(status != ARM_MATH_SUCCESS)
        {
            sys_predict_err_count++;
            return SYS_SENSOR_FUSION_ERR;
        }

        // L_aug *= GAMMA_N
		for(int i=0; i<N*N; i++) L_aug[i] *= GAMMA_N;

        // x_aug = [state; 0; 0; 0]
		float32_t x_aug[N] = {
			ukf.state.px, ukf.state.py, ukf.state.vx, ukf.state.vy, ukf.state.theta,
			ukf.state.b_ax, ukf.state.b_ay, ukf.state.b_gz,
			0, 0, 0 // noise IMU
		};

        // 	if(m==0): 		x_sigma = x_aug
		//  else if(m<=N): 	x_sigma = x_aug + L_aug
		//  else: 			x_sigma = x_aug - L_aug
		for(int m = 0; m < NUM_PREDICT_SIGMA; m++)
		{
			float32_t x_sigma[N];
			if (m == 0)
			{
				for(int i=0; i<N; i++) x_sigma[i] = x_aug[i];
			}
			else if (m <= N)
			{
				for(int i=0; i<N; i++) x_sigma[i] = x_aug[i] + L_aug[i*N + (m-1)];
			}
			else
			{
				for(int i=0; i<N; i++) x_sigma[i] = x_aug[i] - L_aug[i*N + (m-1-N)];
			}

			//	X_sigma_pred = first 8 states (px,py,vx,vy,θ,bax,bay,bgz)
			//  Noise_sigma_pred = last 3 states (n_ax, n_ay, n_gz) = process noise samples
			for(int i=0; i<NUM_STATE; i++) ukf.X_sigma_pred[i][m] = x_sigma[i];
			for(int i=0; i<NUM_PREDICT_NOISE; i++) ukf.Noise_sigma_pred[i][m] = x_sigma[NUM_STATE+i];
		}
		/* NOTE */
		ukf.is_first_frame = false; // Đã tạo xong, các vòng IMU tiếp theo sẽ bỏ qua khối lệnh này
	}

	// LUÔN LUÔN CHẠY: Truyền các điểm Sigma ĐÃ LƯU qua mô hình động học
	for(int m = 0; m < NUM_PREDICT_SIGMA; m++)
	{
		float px = ukf.X_sigma_pred[0][m], py = ukf.X_sigma_pred[1][m];
		float vx = ukf.X_sigma_pred[2][m], vy = ukf.X_sigma_pred[3][m];
		float theta = ukf.X_sigma_pred[4][m];
		float bax = ukf.X_sigma_pred[5][m], bay = ukf.X_sigma_pred[6][m], bgz = ukf.X_sigma_pred[7][m];

		float n_ax = ukf.Noise_sigma_pred[0][m], n_ay = ukf.Noise_sigma_pred[1][m], n_gz = ukf.Noise_sigma_pred[2][m];

		float w_avg = 0.5f * (ukf.imu_old.gz + ukf.imu_current.gz) - bgz + n_gz;
		float theta_new = theta + w_avg * dt;

		float ax_old = (ukf.imu_old.ax - bax + n_ax)*cosf(theta) - (ukf.imu_old.ay - bay + n_ay)*sinf(theta);
		float ay_old = (ukf.imu_old.ax - bax + n_ax)*sinf(theta) + (ukf.imu_old.ay - bay + n_ay)*cosf(theta);

		float ax_new = (ukf.imu_current.ax - bax + n_ax)*cosf(theta_new) - (ukf.imu_current.ay - bay + n_ay)*sinf(theta_new);
		float ay_new = (ukf.imu_current.ax - bax + n_ax)*sinf(theta_new) + (ukf.imu_current.ay - bay + n_ay)*cosf(theta_new);

		float ax_avg = 0.5f * (ax_old + ax_new);
		float ay_avg = 0.5f * (ay_old + ay_new);

		// Cập nhật đè lên chính nó để tích phân tiếp cho vòng IMU sau
		ukf.X_sigma_pred[0][m] = px + vx*dt + 0.5f*ax_avg*dt*dt;
		ukf.X_sigma_pred[1][m] = py + vy*dt + 0.5f*ay_avg*dt*dt;
		ukf.X_sigma_pred[2][m] = vx + ax_avg*dt;
		ukf.X_sigma_pred[3][m] = vy + ay_avg*dt;
		ukf.X_sigma_pred[4][m] = theta_new;
		// Bias không đổi
	}

	// X̄_k = Σ ω_m^{(n)}·X̂_{k,n}
	float32_t x_mean[NUM_STATE] = {0};
	for(int m=0; m<NUM_PREDICT_SIGMA; m++)
	{
		for(int i=0; i<NUM_STATE; i++) x_mean[i] += ukf.Wm_N[m] * ukf.X_sigma_pred[i][m];
	}

    // Theory: P̄_k = Σ ω_c^{(n)}·(X̂_{k,n} - X̄_k)(X̂_{k,n} - X̄_k)ᵀ  (weighted covariance)
	// Code: diff[i] = X_sigma_pred[i][m] - x_mean[i]
	//       P_data += Wc_N[m] * diff[i] * diff[j]
	for(int i=0; i<NUM_STATE*NUM_STATE; i++) ukf.P_data[i] = 0.0f;
	for(int m=0; m<NUM_PREDICT_SIGMA; m++)
	{
		float32_t diff[NUM_STATE];
		for(int i=0; i<NUM_STATE; i++) diff[i] = ukf.X_sigma_pred[i][m] - x_mean[i];
		diff[4] = normalize_angle(diff[4]);

		for(int i=0; i<NUM_STATE; i++)
		{
			for(int j=0; j<NUM_STATE; j++)
			{
				ukf.P_data[i*NUM_STATE + j] += ukf.Wc_N[m] * diff[i] * diff[j];
			}
		}
	}

	// Cập nhật State
	ukf.state.px = x_mean[0]; ukf.state.py = x_mean[1];
	ukf.state.vx = x_mean[2]; ukf.state.vy = x_mean[3];
	ukf.state.theta = normalize_angle(x_mean[4]);
	ukf.state.b_ax = x_mean[5]; ukf.state.b_ay = x_mean[6]; ukf.state.b_gz = x_mean[7];

	ukf.imu_old = ukf.imu_current;

	if (p_ukf != NULL) *p_ukf = ukf.state;
    send_uart_snapshot();
    predict_delta_ms = HAL_GetTick() - sys_predict_tick_ms;

	return SYS_SENSOR_FUSION_OK;
}

sys_sensor_fusion_err_t sys_sensor_fusion_update(sys_sensor_fusion_data_t *p_ukf, float d0, float d1, float d2, uint8_t mask)
{
	CHECK_ERR(p_ukf != NULL, SYS_SENSOR_FUSION_ERR);

    uint32_t sys_update_tick_ms = HAL_GetTick();
	sys_update_count++;

    static float32_t P_aug[M * M];
    static float32_t L_aug[M * M];
    static arm_matrix_instance_f32 mat_Paug, mat_Laug;
    
    memset(P_aug, 0, sizeof(P_aug));
    memset(L_aug, 0, sizeof(L_aug));
    
    arm_mat_init_f32(&mat_Paug, M, M, P_aug);
    arm_mat_init_f32(&mat_Laug, M, M, L_aug);

    // P_aug 	= [P 0]
	// 			= [0 R]
    for(int i=0; i<NUM_STATE; i++)
    {
        for(int j=0; j<NUM_STATE; j++) P_aug[i*M + j] = ukf.P_data[i*NUM_STATE + j];
    }
    for(int i=0; i<NUM_UPDATE_NOISE; i++)
    {
        for(int j=0; j<NUM_UPDATE_NOISE; j++) P_aug[(NUM_STATE+i)*M + (NUM_STATE+j)] = ukf.R_data[i*NUM_UPDATE_NOISE + j];
    }

    // L_aug = chol(P_aug)
    arm_status status = arm_mat_cholesky_f32(&mat_Paug, &mat_Laug);
    if(status != ARM_MATH_SUCCESS)
    {
        sys_update_err_count++;
        sys_update_cholesky_err_count++;
        return SYS_SENSOR_FUSION_ERR;
    }

    // L_aug *= GAMMA_M
    for(int i=0; i<M*M; i++) L_aug[i] *= GAMMA_M;

    static float32_t X_sigma[NUM_STATE][NUM_UPDATE_SIGMA];
    static float32_t D_sigma[NUM_UPDATE_NOISE][NUM_UPDATE_SIGMA];
    
    memset(X_sigma, 0, sizeof(X_sigma));
    memset(D_sigma, 0, sizeof(D_sigma));

    float32_t x_aug[M] = {
        ukf.state.px, ukf.state.py, ukf.state.vx, ukf.state.vy, ukf.state.theta,
        ukf.state.b_ax, ukf.state.b_ay, ukf.state.b_gz,
        0, 0, 0
    };

    float ANCHOR_POS_TABLE[NUM_ANCHORS][2] = {
        {ANCHOR_1_X, ANCHOR_1_Y},
        {ANCHOR_2_X, ANCHOR_2_Y},
        {ANCHOR_3_X, ANCHOR_3_Y},
        {ANCHOR_4_X, ANCHOR_4_Y}
    };

    // if(m==0): 		x_s = x_aug
	// else if(m<=M): 	x_s = x_aug + L_aug
	// else: 			x_s = x_aug - L_aug
    for(int m = 0; m < NUM_UPDATE_SIGMA; m++)
    {
        float32_t x_s[M];
        if (m == 0)
        {
            for(int i=0; i<M; i++) x_s[i] = x_aug[i];
        }
        else if (m <= M)
        {
            for(int i=0; i<M; i++) x_s[i] = x_aug[i] + L_aug[i*M + (m-1)];
        }
        else
        {
            for(int i=0; i<M; i++) x_s[i] = x_aug[i] - L_aug[i*M + (m-1-M)];
        }

        for(int i=0; i<NUM_STATE; i++) X_sigma[i][m] = x_s[i];

        // D_sigma[0][m] = sqrt((px-A1x)²+(py-A1y)²) + x_s[8] (noise term)
        float px = x_s[0], py = x_s[1];
        int d_index = 0;
        for(int anc = 0; anc < NUM_ANCHORS; anc++)
        {
            if(mask & (1 << anc))
            {
                D_sigma[d_index][m] = sqrtf((px - ANCHOR_POS_TABLE[anc][0]) * (px - ANCHOR_POS_TABLE[anc][0]) + 
                                            (py - ANCHOR_POS_TABLE[anc][1]) * (py - ANCHOR_POS_TABLE[anc][1])) + 
                                            x_s[8 + d_index];
                d_index++;
            }
        }    
    }

    // d_mean[i] = Σ Wm_M[m] * D_sigma[i][m]
    float32_t d_mean[NUM_UPDATE_NOISE] = {0};
    for(int m=0; m<NUM_UPDATE_SIGMA; m++)
    {
        for(int i=0; i<NUM_UPDATE_NOISE; i++) d_mean[i] += ukf.Wm_M[m] * D_sigma[i][m];
    }

    // P_dd += Wc_M[m] * diff_d * diff_dᵀ
    // P_xd += Wc_M[m] * diff_x * diff_dᵀ
    static float32_t P_dd[NUM_UPDATE_NOISE * NUM_UPDATE_NOISE];
    static float32_t P_xd[NUM_STATE * NUM_UPDATE_NOISE];
    memset(P_dd, 0, sizeof(P_dd));
    memset(P_xd, 0, sizeof(P_xd));

    for(int m=0; m<NUM_UPDATE_SIGMA; m++)
    {
        float32_t diff_d[NUM_UPDATE_NOISE], diff_x[NUM_STATE];
        for(int i=0; i<NUM_UPDATE_NOISE; i++) diff_d[i] = D_sigma[i][m] - d_mean[i];
        for(int i=0; i<NUM_STATE; i++) diff_x[i] = X_sigma[i][m] - x_aug[i];
        diff_x[4] = normalize_angle(diff_x[4]);

        for(int i=0; i<NUM_UPDATE_NOISE; i++)
        {
            for(int j=0; j<NUM_UPDATE_NOISE; j++) P_dd[i*NUM_UPDATE_NOISE + j] += ukf.Wc_M[m] * diff_d[i] * diff_d[j];
        }

        for(int i=0; i<NUM_STATE; i++)
        {
            for(int j=0; j<NUM_UPDATE_NOISE; j++) P_xd[i*NUM_UPDATE_NOISE + j] += ukf.Wc_M[m] * diff_x[i] * diff_d[j];
        }
    }

    static float32_t P_dd_inv[NUM_UPDATE_NOISE * NUM_UPDATE_NOISE];
    static float32_t K_data[NUM_STATE * NUM_UPDATE_NOISE];
    static arm_matrix_instance_f32 mat_Pdd, mat_Pdd_inv, mat_Pxd, mat_K;
    memset(P_dd_inv, 0, sizeof(P_dd_inv));
    memset(K_data, 0, sizeof(K_data));
    arm_mat_init_f32(&mat_Pdd, NUM_UPDATE_NOISE, NUM_UPDATE_NOISE, P_dd);
    arm_mat_init_f32(&mat_Pdd_inv, NUM_UPDATE_NOISE, NUM_UPDATE_NOISE, P_dd_inv);
    arm_mat_init_f32(&mat_Pxd, NUM_STATE, NUM_UPDATE_NOISE, P_xd);
    arm_mat_init_f32(&mat_K, NUM_STATE, NUM_UPDATE_NOISE, K_data);

    if (arm_mat_inverse_f32(&mat_Pdd, &mat_Pdd_inv) != ARM_MATH_SUCCESS) 
    {
        sys_update_err_count++;
        sys_update_inverse_err_count++;
        return SYS_SENSOR_FUSION_ERR;
    }

    arm_mat_mult_f32(&mat_Pxd, &mat_Pdd_inv, &mat_K);

    float32_t D_real[3] = {d0, d1, d2};
    for(int i=0; i<NUM_STATE; i++)
    {
        float update_val = 0;
        for(int j=0; j<NUM_UPDATE_NOISE; j++)
        {
            update_val += K_data[i*NUM_UPDATE_NOISE + j] * (D_real[j] - d_mean[j]);
        }

        if (i == 0) ukf.state.px += update_val;
        if (i == 1) ukf.state.py += update_val;
        if (i == 2) ukf.state.vx += update_val;
        if (i == 3) ukf.state.vy += update_val;
        if (i == 4) ukf.state.theta = normalize_angle(ukf.state.theta + update_val);
        if (i == 5) ukf.state.b_ax += update_val;
        if (i == 6) ukf.state.b_ay += update_val;
        if (i == 7) ukf.state.b_gz += update_val;
    }

    static float32_t Pxd_t_data[NUM_UPDATE_NOISE * NUM_STATE];
	static float32_t K_Pxd_t_data[NUM_STATE * NUM_STATE];

	static arm_matrix_instance_f32 mat_Pxd_t, mat_K_Pxd_t;
	arm_mat_init_f32(&mat_Pxd_t, NUM_UPDATE_NOISE, NUM_STATE, Pxd_t_data);
	arm_mat_init_f32(&mat_K_Pxd_t, NUM_STATE, NUM_STATE, K_Pxd_t_data);

	// 1. Chuyển vị ma trận Pxd: (P_xd)^T
	arm_mat_trans_f32(&mat_Pxd, &mat_Pxd_t);

	// 2. Nhân K với (P_xd)^T: K * (P_xd)^T
	arm_mat_mult_f32(&mat_K, &mat_Pxd_t, &mat_K_Pxd_t);

	// 3. Cập nhật P: P = P - K * (P_xd)^T
	arm_mat_sub_f32(&ukf.mat_P, &mat_K_Pxd_t, &ukf.mat_P);

    if (p_ukf != NULL) *p_ukf = ukf.state;

    ukf.is_first_frame = true;
	update_delta_ms = HAL_GetTick() - sys_update_tick_ms;
	return SYS_SENSOR_FUSION_OK;
}

sys_sensor_fusion_err_t sys_sensor_fusion_set_initial_position(sys_sensor_fusion_data_t *p_ukf, float x0, float y0)
{
	ukf.state.px = x0;
	ukf.state.py = y0;
	if (p_ukf != NULL) *p_ukf = ukf.state;
	return SYS_SENSOR_FUSION_OK;
}

bool sys_sensor_fusion_is_initialized(void)
{
    return ukf.initialized;
}

bool sys_sensor_fusion_apply_trilateration_result(sys_sensor_fusion_data_t *p_ukf,
                                                  const vec2d_t *tril_position,
                                                  const mw_tril_anchor_t best_3_anchors[3],
                                                  const mw_tril_anchor_t *anchors_by_id,
                                                  const mw_tril_anchor_t *anchors_compact,
                                                  uint8_t compact_count,
                                                  uint8_t selected_anchor_mask)
{
    CHECK_ERR(p_ukf && tril_position && best_3_anchors && anchors_by_id, false);

    s_last_selected_anchors_mask = selected_anchor_mask;
    s_latest_tril_x = (float)tril_position->x;
    s_latest_tril_y = (float)tril_position->y;

    if (!ukf.initialized)
    {
        float init_x, init_y;
        float init_d0, init_d1, init_d2;
        bool pos_done = mw_filter_ukf_init_add(&s_ukf_init_filter,
                                               (float)tril_position->x,
                                               (float)tril_position->y,
                                               &init_x,
                                               &init_y);
        bool dist_done = mw_filter_ukf_init_distance_add(&s_ukf_init_dist_filter,
                                                         (float)best_3_anchors[0].distance,
                                                         (float)best_3_anchors[1].distance,
                                                         (float)best_3_anchors[2].distance,
                                                         &init_d0,
                                                         &init_d1,
                                                         &init_d2);

        if (!pos_done || !dist_done) {
            return false;
        }

        ukf.initialized = true;
        RLOG_I(LOG_OBJECT_CODE_TAG, "[FUSION UKF Init] Tril Px=%.3fm Py=%.3fm Z=%.2fm",
               init_x, init_y, TAG_HEIGHT_M);

        for (int k = 0; k < NUM_ANCHORS; k++) {
            s_latest_distances[k] = 0.0f;
        }
        s_latest_distances[best_3_anchors[0].id - 1] = init_d0;
        s_latest_distances[best_3_anchors[1].id - 1] = init_d1;
        s_latest_distances[best_3_anchors[2].id - 1] = init_d2;

        for (uint8_t k = 0; k < NUM_ANCHORS; k++) {
            s_latest_fp_amp_norm[k] = anchors_by_id[k + 1].fp_amp_norm;
            s_latest_fp_snr[k] = anchors_by_id[k + 1].fp_snr;
        }

        sys_sensor_fusion_set_initial_position(p_ukf, init_x, init_y);
        sys_sensor_fusion_set_predict_flag();
        s_error_count = 0U;
        return true;
    }

    CHECK_ERR(anchors_compact != NULL, false);
    for (int k = 0; k < NUM_ANCHORS; k++) {
        s_latest_distances[k] = 0.0f;
    }
    for (uint8_t k = 0; k < compact_count; k++) {
        uint8_t aid = anchors_compact[k].id;
        if (aid >= 1U && aid <= NUM_ANCHORS) {
            s_latest_distances[aid - 1U] = (float)anchors_compact[k].distance;
        }
    }

    for (uint8_t k = 0; k < NUM_ANCHORS; k++) {
        s_latest_fp_amp_norm[k] = anchors_by_id[k + 1].fp_amp_norm;
        s_latest_fp_snr[k] = anchors_by_id[k + 1].fp_snr;
    }

    if (sys_sensor_fusion_update(p_ukf,
                                 (float)best_3_anchors[0].distance,
                                 (float)best_3_anchors[1].distance,
                                 (float)best_3_anchors[2].distance,
                                 selected_anchor_mask) != SYS_SENSOR_FUSION_OK) {
        return false;
    }

    s_error_count = 0U;
    return true;
}

void sys_sensor_fusion_report_error(void)
{
    s_error_count++;
    s_latest_tril_x = 0.0f;
    s_latest_tril_y = 0.0f;
}

uint32_t sys_sensor_fusion_get_error_count(void)
{
    return s_error_count;
}

void sys_sensor_fusion_reset(void)
{
    RLOG_I(LOG_OBJECT_CODE_TAG, "[FUSION] Resetting sensor fusion filters and state from thread...");
    sys_sensor_fusion_clear_predict_flag();
    sys_sensor_fusion_clear_update_flag();

    sys_sensor_fusion_data_t reset_state = {0};
    if (sys_sensor_fusion_init(&reset_state) != SYS_SENSOR_FUSION_OK)
    {
        RLOG_W(LOG_OBJECT_CODE_TAG, "[FUSION] UKF re-initialization failed");
    }
    else
    {
        RLOG_I(LOG_OBJECT_CODE_TAG, "[FUSION] UKF re-initialized successfully");
    }
}

void sys_sensor_fusion_stream_test_init(network_core_t *stream)
{
#if UKF_BLE_STREAM_TEST_ENABLE
    CHECK_VOID(stream);
    s_stream_test_sample_idx = 0U;
    configure_adv(stream);
#else
    (void)stream;
#endif
}

void sys_sensor_fusion_test_stream_result(network_core_t *stream, bool ranging_enabled)
{
#if UKF_BLE_STREAM_TEST_ENABLE
    CHECK_VOID(stream);

    uint32_t now_ms = HAL_GetTick();

    protobuf_sensor_fusion_result_t stream_data;
    memset(&stream_data, 0, sizeof(stream_data));

    const float start_x = 1.0f;
    const float end_x   = 3.0f;
    const float start_y = 1.0f;
    const float end_y   = 3.0f;
    const float step_m  = 0.05f;

    uint32_t points_per_row = (uint32_t)((end_x - start_x) / step_m) + 1U;
    uint32_t row_count      = (uint32_t)((end_y - start_y) / step_m) + 1U;
    uint32_t total_points   = points_per_row * row_count;

    uint32_t idx = s_stream_test_sample_idx % total_points;
    uint32_t row = idx / points_per_row;
    uint32_t col = idx % points_per_row;

    float x;
    float y = start_y + ((float)row * step_m);

    if ((row % 2U) == 0U)
    {
        x = start_x + ((float)col * step_m);   // hàng chẵn: đi sang phải
        stream_data.ukf_yaw_deg = 0.0f;
    }
    else
    {
        x = end_x - ((float)col * step_m);     // hàng lẻ: đi sang trái
        stream_data.ukf_yaw_deg = 180.0f;
    }

    stream_data.ukf_x_m = x;
    stream_data.ukf_y_m = y;

    /* Trilateration giả lập lệch nhẹ so với UKF */
    stream_data.tril_x_m = stream_data.ukf_y_m + 0.05f;
    stream_data.tril_y_m = stream_data.ukf_x_m - 0.05f;
    stream_data.yaw_deg  = stream_data.ukf_yaw_deg;

    stream_data.ranging_error_count = s_stream_test_sample_idx;
    stream_data.timestamp_ms = now_ms;

    if (network_send_sensor_fusion_result(stream, protobuf_PACKET_ADDR_HOST, &stream_data))
    {
        s_stream_test_sample_idx++;
    }

#else
    (void)stream;
    (void)ranging_enabled;
#endif
}
sys_sensor_fusion_err_t sys_sensor_fusion_set_update_flag()
{
	ukf.enable_update = true;
	return SYS_SENSOR_FUSION_OK;
}

sys_sensor_fusion_err_t sys_sensor_fusion_clear_update_flag()
{
	ukf.enable_update = false;
	return SYS_SENSOR_FUSION_OK;
}

sys_sensor_fusion_err_t sys_sensor_fusion_set_predict_flag()
{
	ukf.enable_predict = true;
	return SYS_SENSOR_FUSION_OK;
}

sys_sensor_fusion_err_t sys_sensor_fusion_clear_predict_flag()
{
	ukf.enable_predict = false;
	return SYS_SENSOR_FUSION_OK;
}

bool sys_sensor_fusion_check_update_flag()
{
	return ukf.enable_update;
}

bool sys_sensor_fusion_check_predict_flag()
{
	return ukf.enable_predict;
}

float sys_sensor_fusion_get_ukf_yaw_deg()
{
	return ukf.state.theta * RAD2DEG;
}

float sys_sensor_fusion_get_yaw_deg()
{
	return yaw * RAD2DEG;
}

/* Private definitions ------------------------------------------------ */
static float calc_dt(void)
{
    uint32_t now = HAL_GetTick();
    float dt = 0.01f;

    if (ukf.has_predict_tick)
    {
        uint32_t dt_ms = now - ukf.last_predict_tick;
        if (dt_ms > 100U) dt_ms = 100U;
        if (dt_ms < 1U) dt_ms = 1U;
        dt = (float)dt_ms / 1000.0f;
    }

    ukf.last_predict_tick = now;
    ukf.has_predict_tick = true;
    return dt;
}

static void reset_runtime_state(void)
{
    mw_filter_ukf_init_reset(&s_ukf_init_filter);
    mw_filter_ukf_init_distance_reset(&s_ukf_init_dist_filter);

    for (uint8_t i = 0; i < NUM_ANCHORS; i++) {
        s_latest_distances[i] = 0.0f;
        s_latest_fp_amp_norm[i] = 0.0;
        s_latest_fp_snr[i] = 0.0;
    }

    s_error_count = 0U;
    s_last_selected_anchors_mask = 0U;
    s_latest_tril_x = 0.0f;
    s_latest_tril_y = 0.0f;
}

static void send_uart_snapshot(void)
{
    if (!ukf.initialized) {
        return;
    }

    float ukf_yaw = sys_sensor_fusion_get_ukf_yaw_deg();
    float raw_yaw = sys_sensor_fusion_get_yaw_deg();

    (void)bsp_io_uart_send_fusion_data(s_last_selected_anchors_mask,
                                       ukf.state.px,
                                       ukf.state.py,
                                       ukf_yaw,
                                       s_latest_tril_x,
                                       s_latest_tril_y,
                                       raw_yaw,
                                       s_error_count);
}

#if UKF_BLE_STREAM_TEST_ENABLE
static void configure_adv(network_core_t *stream)
{
#ifdef HAVE_BLE_PERIPHERAL
    uint32_t sn = bsp_util_get_serial_number();
    char dev_name[32];
    const sys_config_t *p_cfg = sys_config_get();

    if (p_cfg && p_cfg->uwb.role == DEVICE_ROLE_TAG) 
    {
        snprintf(dev_name, sizeof(dev_name), "RTLS-Tag-%u", (unsigned int)p_cfg->uwb.device_id);
    } 
    else if (p_cfg) 
    {
        snprintf(dev_name, sizeof(dev_name), "RTLS-Anchor-%u", (unsigned int)p_cfg->uwb.device_id);
    } 
    else 
    {
        snprintf(dev_name, sizeof(dev_name), "RTLS-Node-%04X", (unsigned int)(sn & 0xFFFF));
    }

    for (int i = 0; i < 5; i++) 
    {
        network_send_ble_adv_config_set(stream, protobuf_PACKET_ADDR_PERIPHERAL, true, sn, dev_name);
        osDelay(50);
    }
#else
    (void)stream;
#endif
}
#endif

static float normalize_angle(float angle)
{
    angle = fmodf(angle, SYS_SENSOR_FUSION_2PI);
    if (angle >  SYS_SENSOR_FUSION_PI) angle -= SYS_SENSOR_FUSION_2PI;
    if (angle < -SYS_SENSOR_FUSION_PI) angle += SYS_SENSOR_FUSION_2PI;
    return angle;
}

/* End of file -------------------------------------------------------- */

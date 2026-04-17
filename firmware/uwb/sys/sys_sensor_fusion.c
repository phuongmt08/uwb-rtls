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
#include "sys_sensor_fusion.h"
#include "bsp_imu.h"
#include "err.h"
#include <stddef.h>
#include <math.h>
#include "sys_logger.h"
#include "log_config.h"

/* Private defines ---------------------------------------------------- */
#define NUM_STATE     			8
#define NUM_PREDICT_NOISE     	3
#define NUM_UPDATE_NOISE     	3
#define N       				(NUM_STATE + NUM_PREDICT_NOISE)
#define M						(NUM_STATE + NUM_UPDATE_NOISE)
#define NUM_PREDICT_SIGMA  		(2*N + 1)
#define NUM_UPDATE_SIGMA  		(2*M + 1)

#define UKF_ALPHA   (1e-3f) // Increased from 1e-3f to prevent catastrophic cancellation in float32
#define UKF_KAPPA   0.0f
#define UKF_BETA    2.0f
#define UKF_LAMBDA_N  			(UKF_ALPHA * UKF_ALPHA * (N + UKF_KAPPA) - N)
#define GAMMA_N       			sqrtf(N + UKF_LAMBDA_N)
#define UKF_LAMBDA_M  			(UKF_ALPHA * UKF_ALPHA * (M + UKF_KAPPA) - M)
#define GAMMA_M       			sqrtf(M + UKF_LAMBDA_M)

#define Qa						(4.066e-5f)
#define Qg						(2.388e-7f)
#define R_uwb					(0.1f)

#define SYS_SENSOR_FUSION_PI    (3.14159265358979323846f)
#define SYS_SENSOR_FUSION_2PI   (2.0f * SYS_SENSOR_FUSION_PI)

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

    // Instances của CMSIS-DSP
    arm_matrix_instance_f32 mat_P;
    arm_matrix_instance_f32 mat_Q;
    arm_matrix_instance_f32 mat_R;

} ukf_core_t;

/* Private macros ----------------------------------------------------- */
/* Public variables --------------------------------------------------- */
static ukf_core_t ukf = {0};

/* Private variables -------------------------------------------------- */
/* Private function prototypes ---------------------------------------- */
static float normalize_angle(float angle);
float d_lambda = 0.0f, d_gamma = 0.0f;
/* Function definitions ----------------------------------------------- */
sys_sensor_fusion_err_t sys_sensor_fusion_init(sys_sensor_fusion_data_t *p_ukf)
{
	CHECK_ERR((bsp_imu_init() == BSP_IMU_OK), SYS_SENSOR_FUSION_ERR);
	CHECK_ERR((p_ukf != NULL), SYS_SENSOR_FUSION_ERR);

	bsp_imu_bias_t imu_bias;

	CHECK_ERR((bsp_imu_get_bias_data(&imu_bias) == BSP_IMU_OK), SYS_SENSOR_FUSION_ERR);

	ukf.state.px = 0.0f;
	ukf.state.py = 0.0f;
	ukf.state.vx = 0.0f;
	ukf.state.vy = 0.0f;
	ukf.state.theta = 0.0f;
	ukf.state.b_ax = imu_bias.bias_ax;
	ukf.state.b_ay = imu_bias.bias_ay;
	ukf.state.b_gz = imu_bias.bias_gz;

	// P
    for(int i=0; i<64; i++) ukf.P_data[i] = 0.0f;
    ukf.P_data[0] 	= 0.1f;  	// p_x
    ukf.P_data[9] 	= 0.1f;   	// p_y
    ukf.P_data[18] 	= 0.1f; 	// v_x
    ukf.P_data[27] 	= 0.1f;  	// v_y
    ukf.P_data[36] 	= 0.1f;  	// theta
    ukf.P_data[45] 	= 1e-3f; 	// Bias
    ukf.P_data[54] 	= 1e-3f;
    ukf.P_data[63] 	= 1e-3f;

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

    // Debug

    d_gamma = GAMMA_M;
    d_lambda = UKF_LAMBDA_M;

    d_gamma = GAMMA_N;
    d_lambda = UKF_LAMBDA_N;

    bsp_imu_get_raw_data(&ukf.imu_old);

    RLOG_I(0x0F, "===== UKF DEFINE =====");
	RLOG_I(0x0F, "NUM_STATE = %d", NUM_STATE);
	RLOG_I(0x0F, "NUM_PREDICT_NOISE = %d", NUM_PREDICT_NOISE);
	RLOG_I(0x0F, "NUM_UPDATE_NOISE = %d", NUM_UPDATE_NOISE);
    RLOG_I(0x0F, "N = %d, M = %d", N, M);
	RLOG_I(0x0F, "NUM_PREDICT_SIGMA = %d", NUM_PREDICT_SIGMA);
	RLOG_I(0x0F, "NUM_UPDATE_SIGMA = %d", NUM_UPDATE_SIGMA);
	RLOG_I(0x0F, "UKF_ALPHA = %f", (double)UKF_ALPHA);
	RLOG_I(0x0F, "UKF_KAPPA = %f", (double)UKF_KAPPA);
	RLOG_I(0x0F, "UKF_BETA = %f", (double)UKF_BETA);
	RLOG_I(0x0F, "UKF_LAMBDA_N = %f, GAMMA_N = %f", (double)UKF_LAMBDA_N, (double)GAMMA_N);
	RLOG_I(0x0F, "UKF_LAMBDA_M = %f, GAMMA_M = %f", (double)UKF_LAMBDA_M, (double)GAMMA_M);
	RLOG_I(0x0F, "Qa = %f, Qg = %f, R_uwb = %f", (double)Qa, (double)Qg, (double)R_uwb);
    HAL_Delay(15); 

	RLOG_I(0x0F, "===== UKF INIT =====");
	RLOG_I(0x0F, "ukf.state = [%.6f, %.6f, %.6f, %.6f, %.6f, %.6f, %.6f, %.6f]",
		   ukf.state.px, ukf.state.py, ukf.state.vx, ukf.state.vy,
		   ukf.state.theta, ukf.state.b_ax, ukf.state.b_ay, ukf.state.b_gz);
    HAL_Delay(10);

	RLOG_I(0x0F, "ukf.P_data = [");
	for(int i = 0; i < NUM_STATE; i++) {
		RLOG_I(0x0F, "[%.6f, %.6f, %.6f, %.6f, %.6f, %.6f, %.6f, %.6f]",
			   ukf.P_data[i*NUM_STATE+0], ukf.P_data[i*NUM_STATE+1], ukf.P_data[i*NUM_STATE+2], ukf.P_data[i*NUM_STATE+3],
			   ukf.P_data[i*NUM_STATE+4], ukf.P_data[i*NUM_STATE+5], ukf.P_data[i*NUM_STATE+6], ukf.P_data[i*NUM_STATE+7]);
        HAL_Delay(5); 
	}
	RLOG_I(0x0F, "]");
    HAL_Delay(10);

	RLOG_I(0x0F, "ukf.Q_data = [");
	for(int i = 0; i < NUM_PREDICT_NOISE; i++) {
		RLOG_I(0x0F, "[%.6f, %.6f, %.6f]", ukf.Q_data[i*NUM_PREDICT_NOISE+0], ukf.Q_data[i*NUM_PREDICT_NOISE+1], ukf.Q_data[i*NUM_PREDICT_NOISE+2]);
	}
	RLOG_I(0x0F, "]");
    HAL_Delay(10);

	RLOG_I(0x0F, "ukf.R_data = [");
	for(int i = 0; i < NUM_UPDATE_NOISE; i++) {
		RLOG_I(0x0F, "[%.6f, %.6f, %.6f]", ukf.R_data[i*NUM_UPDATE_NOISE+0], ukf.R_data[i*NUM_UPDATE_NOISE+1], ukf.R_data[i*NUM_UPDATE_NOISE+2]);
	}
	RLOG_I(0x0F, "]");
    HAL_Delay(10);

	RLOG_I(0x0F, "ukf.Wm_N = [%.6f, %.6f, %.6f, %.6f, %.6f, ...]", ukf.Wm_N[0], ukf.Wm_N[1], ukf.Wm_N[2], ukf.Wm_N[3], ukf.Wm_N[4]);
	RLOG_I(0x0F, "ukf.Wc_N = [%.6f, %.6f, %.6f, %.6f, %.6f, ...]", ukf.Wc_N[0], ukf.Wc_N[1], ukf.Wc_N[2], ukf.Wc_N[3], ukf.Wc_N[4]);

	HAL_Delay(100);

    return SYS_SENSOR_FUSION_OK;
}

uint8_t sys_debug_predict = 0;
uint8_t sys_debug_update = 0;

uint32_t sys_predict_count = 0;
uint32_t sys_update_count = 0;
uint32_t sys_predict_error_at = 0;
uint32_t sys_update_error_at = 0;
bool predict_first_error = true;
bool update_first_error = true;

sys_sensor_fusion_err_t sys_sensor_fusion_predict(sys_sensor_fusion_data_t *p_ukf, float dt)
{
	sys_debug_predict = 0;
    sys_predict_count++;
	CHECK_ERR(p_ukf != NULL, SYS_SENSOR_FUSION_ERR);

	bsp_imu_data_t imu_current = {0};
	if (bsp_imu_get_raw_data(&imu_current) != BSP_IMU_OK)
	{
		sys_debug_predict = 1;
		return SYS_SENSOR_FUSION_ERR;

	}
	sys_debug_predict = 10;

	if (ukf.is_first_frame)
	{
		sys_debug_predict = 20;
		float32_t P_aug[N * N] = {0};
		float32_t L_aug[N * N] = {0};
		arm_matrix_instance_f32 mat_Paug, mat_Laug;
		arm_mat_init_f32(&mat_Paug, N, N, P_aug);
		arm_mat_init_f32(&mat_Laug, N, N, L_aug);

		for(int i=0; i<NUM_STATE; i++)
		{
			for(int j=0; j<NUM_STATE; j++) P_aug[i*N + j] = ukf.P_data[i*NUM_STATE + j];
            }
		for(int i=0; i<NUM_PREDICT_NOISE; i++)
		{
			for(int j=0; j<NUM_PREDICT_NOISE; j++) P_aug[(NUM_STATE+i)*N + (NUM_STATE+j)] = ukf.Q_data[i*NUM_PREDICT_NOISE + j];
		}

		arm_status status = arm_mat_cholesky_f32(&mat_Paug, &mat_Laug);
		if(status != ARM_MATH_SUCCESS)
		{
            if (predict_first_error) {
                predict_first_error = false;
                sys_predict_error_at = sys_predict_count;
                RLOG_E(0x0F, 1, "Predict Cholesky FAILED first time at count %lu!", sys_predict_count);
                RLOG_I(0x0F, "Nghi ngo nguyen nhan: Ma tran P_aug mat tinh xac dinh duong hoac mat can bang do tham so Q/Beta");
                RLOG_I(0x0F, "P_aug = [");
                for(int i = 0; i < N; i++) {
                    RLOG_I(0x0F, "[%.4f, %.4f, %.4f, %.4f, %.4f, %.4f, %.4f, %.4f, %.4f, %.4f, %.4f]",
                        P_aug[i*N+0], P_aug[i*N+1], P_aug[i*N+2], P_aug[i*N+3],
                        P_aug[i*N+4], P_aug[i*N+5], P_aug[i*N+6], P_aug[i*N+7],
                        P_aug[i*N+8], P_aug[i*N+9], P_aug[i*N+10]);
                    HAL_Delay(5);
                }
                RLOG_I(0x0F, "]");
                HAL_Delay(10);
            }
			sys_debug_predict = 21;
			return SYS_SENSOR_FUSION_ERR;
		}
		sys_debug_predict = 22;

		for(int i=0; i<N*N; i++) L_aug[i] *= GAMMA_N;

		float32_t x_aug[N] = {
			ukf.state.px, ukf.state.py, ukf.state.vx, ukf.state.vy, ukf.state.theta,
			ukf.state.b_ax, ukf.state.b_ay, ukf.state.b_gz,
			0, 0, 0 // noise IMU
		};

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

			// LƯU ĐIỂM SIGMA VÀO BIẾN TOÀN CỤC THAY VÌ MẢNG CỤC BỘ
			for(int i=0; i<NUM_STATE; i++) ukf.X_sigma_pred[i][m] = x_sigma[i];
			for(int i=0; i<NUM_PREDICT_NOISE; i++) ukf.Noise_sigma_pred[i][m] = x_sigma[NUM_STATE+i];
		}
		sys_debug_predict = 23;
		/* NOTE */
		ukf.is_first_frame = false; // Đã tạo xong, các vòng IMU tiếp theo sẽ bỏ qua khối lệnh này
		sys_debug_predict = 24;
	}
	sys_debug_predict = 30;
	// LUÔN LUÔN CHẠY: Truyền các điểm Sigma ĐÃ LƯU qua mô hình động học
	for(int m = 0; m < NUM_PREDICT_SIGMA; m++)
	{
		float px = ukf.X_sigma_pred[0][m], py = ukf.X_sigma_pred[1][m];
		float vx = ukf.X_sigma_pred[2][m], vy = ukf.X_sigma_pred[3][m];
		float theta = ukf.X_sigma_pred[4][m];
		float bax = ukf.X_sigma_pred[5][m], bay = ukf.X_sigma_pred[6][m], bgz = ukf.X_sigma_pred[7][m];

		float n_ax = ukf.Noise_sigma_pred[0][m], n_ay = ukf.Noise_sigma_pred[1][m], n_gz = ukf.Noise_sigma_pred[2][m];

		float w_avg = 0.5f * (ukf.imu_old.gz + imu_current.gz) - bgz + n_gz;
		float theta_new = theta + w_avg * dt;

		float ax_old = (ukf.imu_old.ax - bax + n_ax)*cosf(theta) - (ukf.imu_old.ay - bay + n_ay)*sinf(theta);
		float ay_old = (ukf.imu_old.ax - bax + n_ax)*sinf(theta) + (ukf.imu_old.ay - bay + n_ay)*cosf(theta);

		float ax_new = (imu_current.ax - bax + n_ax)*cosf(theta_new) - (imu_current.ay - bay + n_ay)*sinf(theta_new);
		float ay_new = (imu_current.ax - bax + n_ax)*sinf(theta_new) + (imu_current.ay - bay + n_ay)*cosf(theta_new);

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
	sys_debug_predict = 50;
	// Gom lại tính Mean và Covariance
	sys_debug_predict = 60;
	float32_t x_mean[NUM_STATE] = {0};
	for(int m=0; m<NUM_PREDICT_SIGMA; m++)
	{
		for(int i=0; i<NUM_STATE; i++) x_mean[i] += ukf.Wm_N[m] * ukf.X_sigma_pred[i][m];
	}
	sys_debug_predict = 61;
	sys_debug_predict = 70;
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
	sys_debug_predict = 71;

	// Đảm bảo tính đối xứng và xác định dương cho P
	for (int i = 0; i < NUM_STATE; i++) {
	    for (int j = i + 1; j < NUM_STATE; j++) {
	        float avg = 0.5f * (ukf.P_data[i * NUM_STATE + j] + ukf.P_data[j * NUM_STATE + i]);
	        ukf.P_data[i * NUM_STATE + j] = avg;
	        ukf.P_data[j * NUM_STATE + i] = avg;
	    }
	    // Đảm bảo đường chéo tuyệt đối > 0 tránh ma trận mất xác định dương
	    if (ukf.P_data[i * NUM_STATE + i] < 1e-6f) {
	        ukf.P_data[i * NUM_STATE + i] = 1e-6f;
	    }
	}

	sys_debug_predict = 80;
	// Cập nhật State
	ukf.state.px = x_mean[0]; ukf.state.py = x_mean[1];
	ukf.state.vx = x_mean[2]; ukf.state.vy = x_mean[3];
	ukf.state.theta = normalize_angle(x_mean[4]);
	ukf.state.b_ax = x_mean[5]; ukf.state.b_ay = x_mean[6]; ukf.state.b_gz = x_mean[7];
	sys_debug_predict = 90;

	ukf.imu_old = imu_current;

	if (p_ukf != NULL) *p_ukf = ukf.state;
	sys_debug_predict = 99;
	return SYS_SENSOR_FUSION_OK;
}

sys_sensor_fusion_err_t sys_sensor_fusion_update(sys_sensor_fusion_data_t *p_ukf, float d0, float d1, float d2)
{
	sys_update_count++;
	sys_debug_update = 0;
    float32_t P_aug[M * M] = {0};
    float32_t L_aug[M * M] = {0};
    arm_matrix_instance_f32 mat_Paug, mat_Laug;
    arm_mat_init_f32(&mat_Paug, M, M, P_aug);
    arm_mat_init_f32(&mat_Laug, M, M, L_aug);

    for(int i=0; i<NUM_STATE; i++)
    {
        for(int j=0; j<NUM_STATE; j++) P_aug[i*M + j] = ukf.P_data[i*NUM_STATE + j];
    }
    for(int i=0; i<NUM_UPDATE_NOISE; i++)
    {
        for(int j=0; j<NUM_UPDATE_NOISE; j++) P_aug[(NUM_STATE+i)*M + (NUM_STATE+j)] = ukf.R_data[i*NUM_UPDATE_NOISE + j];
    }
    sys_debug_update = 20;
    arm_status status = arm_mat_cholesky_f32(&mat_Paug, &mat_Laug);
    if(status != ARM_MATH_SUCCESS)
    {
        if (update_first_error) {
            update_first_error = false;
            sys_update_error_at = sys_update_count;
            RLOG_E(0x0F, 1, "Update Cholesky FAILED first time at count %lu!", sys_update_count);
            RLOG_I(0x0F, "Nghi ngo nguyen nhan: Buoc Predict gay sai so tich luy cho P_aug => mat tinh xac dinh duong hoac R chua du on dinh.");
            RLOG_I(0x0F, "P_aug = [");
            for(int i = 0; i < M; i++) {
                RLOG_I(0x0F, "[%.4f, %.4f, %.4f, %.4f, %.4f, %.4f, %.4f, %.4f, %.4f, %.4f, %.4f]",
                    P_aug[i*M+0], P_aug[i*M+1], P_aug[i*M+2], P_aug[i*M+3],
                    P_aug[i*M+4], P_aug[i*M+5], P_aug[i*M+6], P_aug[i*M+7],
                    P_aug[i*M+8], P_aug[i*M+9], P_aug[i*M+10]);
                HAL_Delay(5);
            }
            RLOG_I(0x0F, "]");
            HAL_Delay(10);
        }
		sys_debug_update = 21;  // Lỗi Cholesky
		return SYS_SENSOR_FUSION_ERR;
	}
	sys_debug_update = 22;
	sys_debug_update = 30;
    for(int i=0; i<M*M; i++) L_aug[i] *= GAMMA_M;

    float32_t X_sigma[NUM_STATE][NUM_UPDATE_SIGMA] = {0};
    float32_t D_sigma[NUM_UPDATE_NOISE][NUM_UPDATE_SIGMA] = {0};

    float32_t x_aug[M] = {
        ukf.state.px, ukf.state.py, ukf.state.vx, ukf.state.vy, ukf.state.theta,
        ukf.state.b_ax, ukf.state.b_ay, ukf.state.b_gz,
        0, 0, 0
    };

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

        float px = x_s[0], py = x_s[1];
        D_sigma[0][m] = sqrtf((px - ANCHOR_0_X)*(px - ANCHOR_0_X) + (py - ANCHOR_0_Y)*(py - ANCHOR_0_Y)) + x_s[8];
        D_sigma[1][m] = sqrtf((px - ANCHOR_1_X)*(px - ANCHOR_1_X) + (py - ANCHOR_1_Y)*(py - ANCHOR_1_Y)) + x_s[9];
        D_sigma[2][m] = sqrtf((px - ANCHOR_2_X)*(px - ANCHOR_2_X) + (py - ANCHOR_2_Y)*(py - ANCHOR_2_Y)) + x_s[10];
    }
    sys_debug_update = 50;
    sys_debug_update = 60;
    float32_t d_mean[NUM_UPDATE_NOISE] = {0};
    for(int m=0; m<NUM_UPDATE_SIGMA; m++)
    {
        for(int i=0; i<NUM_UPDATE_NOISE; i++) d_mean[i] += ukf.Wm_M[m] * D_sigma[i][m];
    }
    sys_debug_update = 61;
    sys_debug_update = 70;
    float32_t P_dd[NUM_UPDATE_NOISE * NUM_UPDATE_NOISE] = {0};
    float32_t P_xd[NUM_STATE * NUM_UPDATE_NOISE] = {0};

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
    sys_debug_update = 71;

    float32_t P_dd_inv[NUM_UPDATE_NOISE * NUM_UPDATE_NOISE], K_data[NUM_STATE * NUM_UPDATE_NOISE];
    arm_matrix_instance_f32 mat_Pdd, mat_Pdd_inv, mat_Pxd, mat_K;
    arm_mat_init_f32(&mat_Pdd, NUM_UPDATE_NOISE, NUM_UPDATE_NOISE, P_dd);
    arm_mat_init_f32(&mat_Pdd_inv, NUM_UPDATE_NOISE, NUM_UPDATE_NOISE, P_dd_inv);
    arm_mat_init_f32(&mat_Pxd, NUM_STATE, NUM_UPDATE_NOISE, P_xd);
    arm_mat_init_f32(&mat_K, NUM_STATE, NUM_UPDATE_NOISE, K_data);

    if (arm_mat_inverse_f32(&mat_Pdd, &mat_Pdd_inv) != ARM_MATH_SUCCESS)
	{
		sys_debug_update = 80;  // Lỗi inverse
		return SYS_SENSOR_FUSION_ERR;
	}
	sys_debug_update = 81;
	sys_debug_update = 90;
    arm_mat_mult_f32(&mat_Pxd, &mat_Pdd_inv, &mat_K);
    sys_debug_update = 91;
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

    float32_t K_Pdd[NUM_STATE * NUM_UPDATE_NOISE], K_Pdd_Kt[NUM_STATE * NUM_STATE];
    float32_t Kt_data[NUM_UPDATE_NOISE * NUM_STATE];
    arm_matrix_instance_f32 mat_K_Pdd, mat_K_Pdd_Kt, mat_Kt;
    arm_mat_init_f32(&mat_K_Pdd, NUM_STATE, NUM_UPDATE_NOISE, K_Pdd);
    arm_mat_init_f32(&mat_K_Pdd_Kt, NUM_STATE, NUM_STATE, K_Pdd_Kt);
    arm_mat_init_f32(&mat_Kt, NUM_UPDATE_NOISE, NUM_STATE, Kt_data);

    arm_mat_mult_f32(&mat_K, &mat_Pdd, &mat_K_Pdd);
    arm_mat_trans_f32(&mat_K, &mat_Kt);
    arm_mat_mult_f32(&mat_K_Pdd, &mat_Kt, &mat_K_Pdd_Kt);

    arm_mat_sub_f32(&ukf.mat_P, &mat_K_Pdd_Kt, &ukf.mat_P);

    // Đảm bảo tính đối xứng và xác định dương cho P
    for (int i = 0; i < NUM_STATE; i++) {
        for (int j = i + 1; j < NUM_STATE; j++) {
            float avg = 0.5f * (ukf.P_data[i * NUM_STATE + j] + ukf.P_data[j * NUM_STATE + i]);
            ukf.P_data[i * NUM_STATE + j] = avg;
            ukf.P_data[j * NUM_STATE + i] = avg;
        }
        // Đảm bảo đường chéo tuyệt đối > 0 tránh ma trận mất xác định dương
        if (ukf.P_data[i * NUM_STATE + i] < 1e-6f) {
            ukf.P_data[i * NUM_STATE + i] = 1e-6f;
        }
    }

    if (p_ukf != NULL) *p_ukf = ukf.state;

    ukf.is_first_frame = true;
    sys_debug_update = 199;
    return SYS_SENSOR_FUSION_OK;
}

/* Private definitions ------------------------------------------------ */
static float normalize_angle(float angle)
{
    angle = fmodf(angle, SYS_SENSOR_FUSION_2PI);
    if (angle >  SYS_SENSOR_FUSION_PI) angle -= SYS_SENSOR_FUSION_2PI;
    if (angle < -SYS_SENSOR_FUSION_PI) angle += SYS_SENSOR_FUSION_2PI;
    return angle;
}

/* End of file -------------------------------------------------------- */

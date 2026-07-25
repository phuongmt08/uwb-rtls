"""UKF planar covariance validation, confidence metrics, and frame rotation."""

from __future__ import annotations

import math
from typing import Mapping


CHI_SQUARE_2D_95 = 5.991464547107979
CONFIDENCE_HIGH_STD_M = 0.20
CONFIDENCE_TRUSTED_STD_M = 0.50


def covariance_metrics(
    pxx: float,
    pxy: float,
    pyy: float,
    valid: bool = True,
) -> dict:
    """Return stable 2D uncertainty metrics derived from a covariance matrix."""
    result = {
        "position_cov_xx_m2": float(pxx),
        "position_cov_xy_m2": float(pxy),
        "position_cov_yy_m2": float(pyy),
        "position_cov_valid": False,
        "position_std_m": 0.0,
        "position_sigma_major_m": 0.0,
        "position_sigma_minor_m": 0.0,
        "position_ellipse_major_95_m": 0.0,
        "position_ellipse_minor_95_m": 0.0,
        "position_ellipse_angle_deg": 0.0,
        "position_confidence": "Unavailable",
    }
    if not valid or not all(math.isfinite(value) for value in (pxx, pxy, pyy)):
        return result

    trace = pxx + pyy
    discriminant = (pxx - pyy) * (pxx - pyy) + 4.0 * pxy * pxy
    if pxx < 0.0 or pyy < 0.0 or trace < 0.0 or discriminant < 0.0:
        return result

    root = math.sqrt(max(0.0, discriminant))
    lambda_major = 0.5 * (trace + root)
    lambda_minor = 0.5 * (trace - root)
    tolerance = max(1.0e-9, abs(trace) * 1.0e-6)
    if lambda_major < -tolerance or lambda_minor < -tolerance:
        return result

    lambda_major = max(0.0, lambda_major)
    lambda_minor = max(0.0, lambda_minor)
    sigma_major = math.sqrt(lambda_major)
    sigma_minor = math.sqrt(lambda_minor)
    position_std = math.sqrt(max(0.0, trace))
    ellipse_scale = math.sqrt(CHI_SQUARE_2D_95)

    if position_std <= CONFIDENCE_HIGH_STD_M:
        confidence = "High"
    elif position_std <= CONFIDENCE_TRUSTED_STD_M:
        confidence = "Medium"
    else:
        confidence = "Low"

    result.update({
        "position_cov_valid": True,
        "position_std_m": position_std,
        "position_sigma_major_m": sigma_major,
        "position_sigma_minor_m": sigma_minor,
        "position_ellipse_major_95_m": ellipse_scale * sigma_major,
        "position_ellipse_minor_95_m": ellipse_scale * sigma_minor,
        "position_ellipse_angle_deg": math.degrees(
            0.5 * math.atan2(2.0 * pxy, pxx - pyy)
        ),
        "position_confidence": confidence,
    })
    return result


def metrics_from_payload(payload: Mapping) -> dict:
    return covariance_metrics(
        float(payload.get("position_cov_xx_m2", 0.0) or 0.0),
        float(payload.get("position_cov_xy_m2", 0.0) or 0.0),
        float(payload.get("position_cov_yy_m2", 0.0) or 0.0),
        bool(payload.get("position_cov_valid", False)),
    )


def rotate_covariance_metrics(payload: Mapping, yaw_deg: float) -> dict:
    """Rotate covariance into a scene frame using P' = R P R^T."""
    if not bool(payload.get("position_cov_valid", False)):
        return metrics_from_payload(payload)

    pxx = float(payload.get("position_cov_xx_m2", 0.0) or 0.0)
    pxy = float(payload.get("position_cov_xy_m2", 0.0) or 0.0)
    pyy = float(payload.get("position_cov_yy_m2", 0.0) or 0.0)
    theta = math.radians(float(yaw_deg))
    cos_theta = math.cos(theta)
    sin_theta = math.sin(theta)

    rotated_xx = (
        cos_theta * cos_theta * pxx
        - 2.0 * cos_theta * sin_theta * pxy
        + sin_theta * sin_theta * pyy
    )
    rotated_xy = (
        cos_theta * sin_theta * (pxx - pyy)
        + (cos_theta * cos_theta - sin_theta * sin_theta) * pxy
    )
    rotated_yy = (
        sin_theta * sin_theta * pxx
        + 2.0 * cos_theta * sin_theta * pxy
        + cos_theta * cos_theta * pyy
    )
    return covariance_metrics(rotated_xx, rotated_xy, rotated_yy, True)

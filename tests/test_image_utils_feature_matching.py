import numpy as np
import pytest

from utils.image_utils import ImageUtils


def test_feature_matching_with_blank_target_does_not_crash():
    """验证当目标图像为全黑/无特征区域时，不会触发 OpenCV randShuffle (-215) 断言崩溃，
    而是优雅返回 False, 0。"""
    # 构造带有简单纹理的模板
    template = np.zeros((100, 100, 3), dtype=np.uint8)
    template[20:80, 20:80] = 255
    template[40:60, 40:60] = 128

    # 构造完全无特征的全黑目标图（裁剪到黑边或迷雾）
    blank_target = np.zeros((100, 100, 3), dtype=np.uint8)

    matched, count = ImageUtils.feature_matching(template, blank_target, min_matches=8)
    assert matched is False
    assert count == 0


def test_feature_matching_with_both_blank_images():
    """验证两张全黑图像比较不会抛出异常"""
    blank1 = np.zeros((50, 50, 3), dtype=np.uint8)
    blank2 = np.zeros((50, 50, 3), dtype=np.uint8)

    matched, count = ImageUtils.feature_matching(blank1, blank2, min_matches=8)
    assert matched is False
    assert count == 0

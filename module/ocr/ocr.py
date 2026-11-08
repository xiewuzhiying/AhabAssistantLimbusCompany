import logging
import os
from pathlib import Path

import cv2
import numpy as np
from cv2 import createCLAHE
from PIL import Image
from rapidocr import EngineType, LangDet, LangRec, ModelType, OCRVersion, RapidOCR
from rapidocr.utils.output import RapidOCROutput

from utils.singletonmeta import SingletonMeta


class OCR(metaclass=SingletonMeta):
    def __init__(self, logger: logging.Logger):
        self.logger = logger
        self._engine: RapidOCR | None = None

    @property
    def engine(self) -> RapidOCR:
        if self._engine is None:
            self._engine = self._init_engine()
        return self._engine

    def _init_engine(self) -> RapidOCR:
        xdg_cache = os.environ.get("XDG_CACHE_HOME")
        cache_base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
        cache_dir = cache_base / "rapidocr"
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        params: dict[str, str | EngineType | LangDet | LangRec | ModelType | OCRVersion] = {
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.lang_type": LangDet.CH,
            "Det.model_type": ModelType.MOBILE,
            "Det.ocr_version": OCRVersion.PPOCRV4,
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.lang_type": LangRec.CH,
            "Rec.model_type": ModelType.MOBILE,
            "Rec.ocr_version": OCRVersion.PPOCRV4,
        }
        if cache_dir.exists():
            params["Global.model_root_dir"] = str(cache_dir)

        project_root = Path(__file__).resolve().parents[2]
        config_path = project_root / "assets" / "config" / "default_rapidocr.yaml"
        try:
            return RapidOCR(
                params=params,
                config_path=str(config_path) if config_path.exists() else None,
            )
        except Exception as e:
            self.logger.warning(f"使用自定义配置初始化 RapidOCR 失败，回退至默认配置: {e}")
            return RapidOCR()

    def run(self, image: Image.Image | np.ndarray | str) -> RapidOCROutput:
        """执行OCR识别，支持Image对象、文件路径和np.ndarray对象"""
        if image is None:
            return RapidOCROutput()
        try:
            if isinstance(image, str):
                with Image.open(image) as image_file:
                    image_array = np.array(image_file)
            elif isinstance(image, Image.Image):
                image_array = np.array(image)
            elif isinstance(image, np.ndarray):
                image_array = image
            else:
                image_array = np.array(image)

            if image_array.ndim == 2:
                img_cv_gray = image_array
            elif image_array.ndim == 3:
                channel_count = image_array.shape[2]
                if channel_count == 1:
                    img_cv_gray = image_array[:, :, 0]
                elif channel_count == 3:
                    img_cv = cv2.cvtColor(image_array, cv2.COLOR_RGB2BGR)
                    img_cv_gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
                elif channel_count == 4:
                    img_cv = cv2.cvtColor(image_array, cv2.COLOR_RGBA2BGR)
                    img_cv_gray = cv2.cvtColor(img_cv, cv2.COLOR_BGR2GRAY)
                else:
                    raise ValueError(f"不支持的图像通道数: {channel_count}")
            else:
                raise ValueError(f"不支持的图像维度: {image_array.ndim}")

            # 自适应均衡化(均值化后更亮)
            clahe = createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            processed_image = clahe.apply(img_cv_gray)
            try:
                results = self.engine(processed_image)
            except Exception:
                results = self.engine(image_array)
            if not results.txts:
                try:
                    alt_results = self.engine(image_array)
                    if alt_results.txts:
                        results = alt_results
                except Exception:
                    pass
            self.log_results(results)
            return results
        except Exception as e:
            self.logger.error(e)
            return RapidOCROutput()

    def log_results(self, ocr_results: RapidOCROutput) -> None:
        """记录OCR识别记录"""
        self.logger.debug(f"OCR识别结果：{ocr_results.txts}")

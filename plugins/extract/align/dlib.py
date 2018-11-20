#!/usr/bin/env python3
""" DLib landmarks extractor for faceswap.py
"""
import face_recognition_models
import dlib

from ._base import Aligner


class Align(Aligner):
    """ Perform transformation to align and get landmarks """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.vram = 0  # Doesn't use GPU
        self.model = None

    def set_model_path(self):
        """ Model path handled by face_recognition_models """
        return face_recognition_models.pose_predictor_model_location()

    def initialize(self, *args, **kwargs):
        """ Initialization tasks to run prior to alignments """
        super().initialize(*args, **kwargs)
        print("Initializing Dlib Pose Predictor...")
        self.model = dlib.shape_predictor(self.model_path)  # pylint: disable=c-extension-no-member
        self.init.set()
        print("Initialized Dlib Pose Predictor.")

    def align(self, *args, **kwargs):
        """ Perform alignments on detected faces """
        super().align(*args, **kwargs)
        while True:
            item = self.queues["in"].get()
            if item == "EOF":
                break
            if item.get("exception", False):
                self.queues["out"].put(item)
                break
            image = item["image"][:, :, ::-1].copy()
            item["detected_faces"] = self.process_landmarks(image, item["detected_faces"])
            self.finalize(item)
        self.finalize("EOF")

    def process_landmarks(self, image, detected_faces):
        """ Align image and process landmarks """
        retval = list()
        for detected_face in detected_faces:
            pts = self.model(image, detected_face).parts()
            landmarks = [(point.x, point.y) for point in pts]
            retval.append((detected_face, landmarks))
        return retval

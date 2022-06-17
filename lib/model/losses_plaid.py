#!/usr/bin/env python3
""" Custom Loss Functions for faceswap.py """

from __future__ import absolute_import

import logging
from typing import List, Tuple

import numpy as np
import plaidml
import tensorflow as tf

from keras import backend as K
from lib.plaidml_utils import pad
from lib.utils import FaceswapError

logger = logging.getLogger(__name__)  # pylint:disable=invalid-name


class DSSIMObjective():  # pylint:disable=too-few-public-methods
    """ DSSIM and MS-DSSIM Loss Functions

    Difference of Structural Similarity (DSSIM loss function).

    Adapted from :func:`tensorflow.image.ssim` for a pure keras implentation.

    Notes
    -----
    Channels last only. Assumes all input images are the same size and square

    Parameters
    ----------
    k_1: float, optional
        Parameter of the SSIM. Default: `0.01`
    k_2: float, optional
        Parameter of the SSIM. Default: `0.03`
    filter_size: int, optional
        size of gaussian filter Default: `11`
    filter_sigma: float, optional
        Width of gaussian filter Default: `1.5`
    max_value: float, optional
        Max value of the output. Default: `1.0`

    Notes
    ------
    You should add a regularization term like a l2 loss in addition to this one.
    """
    def __init__(self,
                 k_1: float = 0.01,
                 k_2: float = 0.03,
                 filter_size: int = 11,
                 filter_sigma: float = 1.5,
                 max_value: float = 1.0) -> None:
        self._filter_size = filter_size
        self._filter_sigma = filter_sigma
        self._kernel = self._get_kernel()

        compensation = 1.0
        self._c1 = (k_1 * max_value) ** 2
        self._c2 = ((k_2 * max_value) ** 2) * compensation

    def _get_kernel(self) -> plaidml.tile.Value:
        """ Obtain the base kernel for performing depthwise convolution.

        Returns
        -------
        :class:`plaidml.tile.Value`
            The gaussian kernel based on selected size and sigma
        """
        coords = np.arange(self._filter_size, dtype="float32")
        coords -= (self._filter_size - 1) / 2.

        kernel = np.square(coords)
        kernel *= -0.5 / np.square(self._filter_sigma)
        kernel = np.reshape(kernel, (1, -1)) + np.reshape(kernel, (-1, 1))
        kernel = K.constant(np.reshape(kernel, (1, -1)))
        kernel = K.softmax(kernel)
        kernel = K.reshape(kernel, (self._filter_size, self._filter_size, 1, 1))
        return kernel

    @classmethod
    def _depthwise_conv2d(cls,
                          image: plaidml.tile.Value,
                          kernel: plaidml.tile.Value) -> plaidml.tile.Value:
        """ Perform a standardized depthwise convolution.

        Parameters
        ----------
        image: :class:`plaidml.tile.Value`
            Batch of images, channels last, to perform depthwise convolution
        kernel: :class:`plaidml.tile.Value`
            convolution kernel

        Returns
        -------
        :class:`plaidml.tile.Value`
            The output from the convolution
        """
        return K.depthwise_conv2d(image, kernel, strides=(1, 1), padding="valid")

    def _get_ssim(self,
                  y_true: plaidml.tile.Value,
                  y_pred: plaidml.tile.Value) -> Tuple[plaidml.tile.Value, plaidml.tile.Value]:
        """ Obtain the structural similarity between a batch of true and predicted images.

        Parameters
        ----------
        y_true: :class:`plaidml.tile.Value`
            The input batch of ground truth images
        y_pred: :class:`plaidml.tile.Value`
            The input batch of predicted images

        Returns
        -------
        :class:`plaidml.tile.Value`
            The SSIM for the given images
        :class:`plaidml.tile.Value`
            The Contrast for the given images
        """
        channels = K.int_shape(y_pred)[-1]
        kernel = K.tile(self._kernel, (1, 1, channels, 1))

        # SSIM luminance measure is (2 * mu_x * mu_y + c1) / (mu_x ** 2 + mu_y ** 2 + c1)
        mean_true = self._depthwise_conv2d(y_true, kernel)
        mean_pred = self._depthwise_conv2d(y_pred, kernel)
        num_lum = mean_true * mean_pred * 2.0
        den_lum = K.square(mean_true) + K.square(mean_pred)
        luminance = (num_lum + self._c1) / (den_lum + self._c1)

        # SSIM contrast-structure measure is (2 * cov_{xy} + c2) / (cov_{xx} + cov_{yy} + c2)
        num_con = self._depthwise_conv2d(y_true * y_pred, kernel) * 2.0
        den_con = self._depthwise_conv2d(K.square(y_true) + K.square(y_pred), kernel)

        contrast = (num_con - num_lum + self._c2) / (den_con - den_lum + self._c2)

        # Average over the height x width dimensions
        axes = (-3, -2)
        ssim = K.mean(luminance * contrast, axis=axes)
        contrast = K.mean(contrast, axis=axes)

        return ssim, contrast

    def __call__(self,
                 y_true: plaidml.tile.Value,
                 y_pred: plaidml.tile.Value) -> plaidml.tile.Value:
        """ Call the DSSIM  or MS-DSSIM Loss Function.

        Parameters
        ----------
        y_true: :class:`plaidml.tile.Value`
            The input batch of ground truth images
        y_pred: :class:`plaidml.tile.Value`
            The input batch of predicted images

        Returns
        -------
        :class:`plaidml.tile.Value`
            The DSSIM or MS-DSSIM for the given images
        """
        ssim = self._get_ssim(y_true, y_pred)[0]
        retval = (1. - ssim) / 2.0
        return K.mean(retval)


class MSSIMLoss(DSSIMObjective):  # pylint:disable=too-few-public-methods
    """ Multiscale Structural Similarity Loss Function

    Parameters
    ----------
    k_1: float, optional
        Parameter of the SSIM. Default: `0.01`
    k_2: float, optional
        Parameter of the SSIM. Default: `0.03`
    filter_size: int, optional
        size of gaussian filter Default: `11`
    filter_sigma: float, optional
        Width of gaussian filter Default: `1.5`
    max_value: float, optional
        Max value of the output. Default: `1.0`
    power_factors: tuple, optional
        Iterable of weights for each of the scales. The number of scales used is the length of the
        list. Index 0 is the unscaled resolution's weight and each increasing scale corresponds to
        the image being downsampled by 2. Defaults to the values obtained in the original paper.
        Default: (0.0448, 0.2856, 0.3001, 0.2363, 0.1333)

    Notes
    ------
    You should add a regularization term like a l2 loss in addition to this one.
    """
    def __init__(self,
                 k_1: float = 0.01,
                 k_2: float = 0.03,
                 filter_size: int = 11,
                 filter_sigma: float = 1.5,
                 max_value: float = 1.0,
                 power_factors: Tuple[float, ...] = (0.0448, 0.2856, 0.3001, 0.2363, 0.1333)
                 ) -> None:
        super().__init__(k_1=k_1,
                         k_2=k_2,
                         filter_size=filter_size,
                         filter_sigma=filter_sigma,
                         max_value=max_value)
        self._power_factors = K.constant(power_factors)

    def _get_smallest_size(self, size: int, idx: int) -> int:
        """ Recursive function to obtain the smallest size that the image will be scaled to.
        for MS-SSIM

        Parameters
        ----------
        size: int
            The current scaled size to iterate through
        idx: int
            The current iteration to be performed. When iteration hits zero the value will
            be returned

        Returns
        -------
        int
            The smallest size the image will be scaled to based on the original image size and
            the amount of scaling factors that will occur
        """
        logger.debug("scale id: %s, size: %s", idx, size)
        if idx > 0:
            size = self._get_smallest_size(size // 2, idx - 1)
        return size

    @classmethod
    def _shrink_images(cls, images: List[plaidml.tile.Value]) -> List[plaidml.tile.Value]:
        """ Reduce the dimensional space of a batch of images in half. If the images are an odd
        number of pixels then pad them to an even dimension prior to shrinking

        All incoming images are assumed square.

        Parameters
        ----------
        images: list
            The y_true, y_pred batch of images to be shrunk

        Returns
        -------
        list
            The y_true, y_pred batch shrunk by half
        """
        if any(x % 2 != 0 for x in K.int_shape(images[1])[1:2]):
            images = [pad(img,
                          [[0, 0], [0, 1], [0, 1], [0, 0]],
                          mode="REFLECT")
                      for img in images]

        images = [K.pool2d(img, (2, 2), strides=(2, 2), padding="valid", pool_mode="avg")
                  for img in images]

        return images

    def _get_ms_ssim(self,
                     y_true: plaidml.tile.Value,
                     y_pred: plaidml.tile.Value) -> plaidml.tile.Value:
        """ Obtain the Multiscale Stuctural Similarity metric.

        Parameters
        ----------
        y_true: :class:`plaidml.tile.Value`
            The input batch of ground truth images
        y_pred: :class:`plaidml.tile.Value`
            The input batch of predicted images

        Returns
        -------
        :class:`plaidml.tile.Value`
            The MS-SSIM for the given images
        """
        im_size = K.int_shape(y_pred)[1]
        # filter size cannot be larger than the smallest scale
        recursions = K.int_shape(self._power_factors)[0]
        smallest_scale = self._get_smallest_size(im_size, recursions - 1)
        if smallest_scale < self._filter_size:
            self._filter_size = smallest_scale
            self._kernel = self._get_kernel()

        images = [y_true, y_pred]
        contrasts = []

        for idx in range(recursions):
            images = self._shrink_images(images) if idx > 0 else images
            ssim, contrast = self._get_ssim(*images)

            if idx < recursions - 1:
                contrasts.append(K.relu(K.expand_dims(contrast, axis=-1)))

        contrasts.append(K.relu(K.expand_dims(ssim, axis=-1)))
        mcs_and_ssim = K.concatenate(contrasts, axis=-1)
        ms_ssim = K.pow(mcs_and_ssim, self._power_factors)

        # K.prod does not work in plaidml so slow recursion it is
        out = ms_ssim[..., 0]
        for idx in range(1, recursions):
            out *= ms_ssim[..., idx]
        return out

    def __call__(self, y_true, y_pred):
        """ Call the MS-SSIM Loss Function.

        Parameters
        ----------
        y_true: tensor or variable
            The ground truth value
        y_pred: tensor or variable
            The predicted value

        Returns
        -------
        tensor
            The MS-SSIM Loss value
        """
        ms_ssim = self._get_ms_ssim(y_true, y_pred)
        retval = 1. - ms_ssim
        return K.mean(retval)


class GeneralizedLoss():  # pylint:disable=too-few-public-methods
    """  Generalized function used to return a large variety of mathematical loss functions.

    The primary benefit is a smooth, differentiable version of L1 loss.

    References
    ----------
    Barron, J. A More General Robust Loss Function - https://arxiv.org/pdf/1701.03077.pdf

    Example
    -------
    >>> a=1.0, x>>c , c=1.0/255.0  # will give a smoothly differentiable version of L1 / MAE loss
    >>> a=1.999999 (limit as a->2), beta=1.0/255.0 # will give L2 / RMSE loss

    Parameters
    ----------
    alpha: float, optional
        Penalty factor. Larger number give larger weight to large deviations. Default: `1.0`
    beta: float, optional
        Scale factor used to adjust to the input scale (i.e. inputs of mean `1e-4` or `256`).
        Default: `1.0/255.0`
    """
    def __init__(self, alpha=1.0, beta=1.0/255.0):
        self.alpha = alpha
        self.beta = beta

    def __call__(self, y_true, y_pred):
        """ Call the Generalized Loss Function

        Parameters
        ----------
        y_true: tensor or variable
            The ground truth value
        y_pred: tensor or variable
            The predicted value

        Returns
        -------
        tensor
            The loss value from the results of function(y_pred - y_true)
        """
        diff = y_pred - y_true
        second = (K.pow(K.pow(diff/self.beta, 2.) / K.abs(2. - self.alpha) + 1.,
                        (self.alpha / 2.)) - 1.)
        loss = (K.abs(2. - self.alpha)/self.alpha) * second
        loss = K.mean(loss, axis=-1) * self.beta
        return loss


class LInfNorm():  # pylint:disable=too-few-public-methods
    """ Calculate the L-inf norm as a loss function. """

    def __call__(self, y_true, y_pred):
        """ Call the L-inf norm loss function.

        Parameters
        ----------
        y_true: tensor or variable
            The ground truth value
        y_pred: tensor or variable
            The predicted value

        Returns
        -------
        tensor
            The loss value
        """
        diff = K.abs(y_true - y_pred)
        max_loss = K.max(diff, axis=(1, 2), keepdims=True)
        loss = K.mean(max_loss, axis=-1)
        return loss


class GradientLoss():  # pylint:disable=too-few-public-methods
    """ Gradient Loss Function.

    Calculates the first and second order gradient difference between pixels of an image in the x
    and y dimensions. These gradients are then compared between the ground truth and the predicted
    image and the difference is taken. When used as a loss, its minimization will result in
    predicted images approaching the same level of sharpness / blurriness as the ground truth.

    References
    ----------
    TV+TV2 Regularization with Non-Convex Sparseness-Inducing Penalty for Image Restoration,
    Chengwu Lu & Hua Huang, 2014 - http://downloads.hindawi.com/journals/mpe/2014/790547.pdf
    """
    def __init__(self):
        self.generalized_loss = GeneralizedLoss(alpha=1.9999)

    def __call__(self, y_true, y_pred):
        """ Call the gradient loss function.

        Parameters
        ----------
        y_true: tensor or variable
            The ground truth value
        y_pred: tensor or variable
            The predicted value

        Returns
        -------
        tensor
            The loss value
        """
        tv_weight = 1.0
        tv2_weight = 1.0
        loss = 0.0
        loss += tv_weight * (self.generalized_loss(self._diff_x(y_true), self._diff_x(y_pred)) +
                             self.generalized_loss(self._diff_y(y_true), self._diff_y(y_pred)))
        loss += tv2_weight * (self.generalized_loss(self._diff_xx(y_true), self._diff_xx(y_pred)) +
                              self.generalized_loss(self._diff_yy(y_true), self._diff_yy(y_pred)) +
                              self.generalized_loss(self._diff_xy(y_true), self._diff_xy(y_pred))
                              * 2.)
        loss = loss / (tv_weight + tv2_weight)
        # TODO simplify to use MSE instead
        return loss

    @classmethod
    def _diff_x(cls, img):
        """ X Difference """
        x_left = img[:, :, 1:2, :] - img[:, :, 0:1, :]
        x_inner = img[:, :, 2:, :] - img[:, :, :-2, :]
        x_right = img[:, :, -1:, :] - img[:, :, -2:-1, :]
        x_out = K.concatenate([x_left, x_inner, x_right], axis=2)
        return x_out * 0.5

    @classmethod
    def _diff_y(cls, img):
        """ Y Difference """
        y_top = img[:, 1:2, :, :] - img[:, 0:1, :, :]
        y_inner = img[:, 2:, :, :] - img[:, :-2, :, :]
        y_bot = img[:, -1:, :, :] - img[:, -2:-1, :, :]
        y_out = K.concatenate([y_top, y_inner, y_bot], axis=1)
        return y_out * 0.5

    @classmethod
    def _diff_xx(cls, img):
        """ X-X Difference """
        x_left = img[:, :, 1:2, :] + img[:, :, 0:1, :]
        x_inner = img[:, :, 2:, :] + img[:, :, :-2, :]
        x_right = img[:, :, -1:, :] + img[:, :, -2:-1, :]
        x_out = K.concatenate([x_left, x_inner, x_right], axis=2)
        return x_out - 2.0 * img

    @classmethod
    def _diff_yy(cls, img):
        """ Y-Y Difference """
        y_top = img[:, 1:2, :, :] + img[:, 0:1, :, :]
        y_inner = img[:, 2:, :, :] + img[:, :-2, :, :]
        y_bot = img[:, -1:, :, :] + img[:, -2:-1, :, :]
        y_out = K.concatenate([y_top, y_inner, y_bot], axis=1)
        return y_out - 2.0 * img

    @classmethod
    def _diff_xy(cls, img):
        """ X-Y Difference """
        # xout1
        top_left = img[:, 1:2, 1:2, :] + img[:, 0:1, 0:1, :]
        inner_left = img[:, 2:, 1:2, :] + img[:, :-2, 0:1, :]
        bot_left = img[:, -1:, 1:2, :] + img[:, -2:-1, 0:1, :]
        xy_left = K.concatenate([top_left, inner_left, bot_left], axis=1)

        top_mid = img[:, 1:2, 2:, :] + img[:, 0:1, :-2, :]
        mid_mid = img[:, 2:, 2:, :] + img[:, :-2, :-2, :]
        bot_mid = img[:, -1:, 2:, :] + img[:, -2:-1, :-2, :]
        xy_mid = K.concatenate([top_mid, mid_mid, bot_mid], axis=1)

        top_right = img[:, 1:2, -1:, :] + img[:, 0:1, -2:-1, :]
        inner_right = img[:, 2:, -1:, :] + img[:, :-2, -2:-1, :]
        bot_right = img[:, -1:, -1:, :] + img[:, -2:-1, -2:-1, :]
        xy_right = K.concatenate([top_right, inner_right, bot_right], axis=1)

        # Xout2
        top_left = img[:, 0:1, 1:2, :] + img[:, 1:2, 0:1, :]
        inner_left = img[:, :-2, 1:2, :] + img[:, 2:, 0:1, :]
        bot_left = img[:, -2:-1, 1:2, :] + img[:, -1:, 0:1, :]
        xy_left = K.concatenate([top_left, inner_left, bot_left], axis=1)

        top_mid = img[:, 0:1, 2:, :] + img[:, 1:2, :-2, :]
        mid_mid = img[:, :-2, 2:, :] + img[:, 2:, :-2, :]
        bot_mid = img[:, -2:-1, 2:, :] + img[:, -1:, :-2, :]
        xy_mid = K.concatenate([top_mid, mid_mid, bot_mid], axis=1)

        top_right = img[:, 0:1, -1:, :] + img[:, 1:2, -2:-1, :]
        inner_right = img[:, :-2, -1:, :] + img[:, 2:, -2:-1, :]
        bot_right = img[:, -2:-1, -1:, :] + img[:, -1:, -2:-1, :]
        xy_right = K.concatenate([top_right, inner_right, bot_right], axis=1)

        xy_out1 = K.concatenate([xy_left, xy_mid, xy_right], axis=2)
        xy_out2 = K.concatenate([xy_left, xy_mid, xy_right], axis=2)
        return (xy_out1 - xy_out2) * 0.25


class GMSDLoss():  # pylint:disable=too-few-public-methods
    """ Gradient Magnitude Similarity Deviation Loss.

    Improved image quality metric over MS-SSIM with easier calculations

    References
    ----------
    http://www4.comp.polyu.edu.hk/~cslzhang/IQA/GMSD/GMSD.htm
    https://arxiv.org/ftp/arxiv/papers/1308/1308.3052.pdf
    """

    def __call__(self, y_true, y_pred):
        """ Return the Gradient Magnitude Similarity Deviation Loss.

        Parameters
        ----------
        y_true: tensor or variable
            The ground truth value
        y_pred: tensor or variable
            The predicted value

        Returns
        -------
        tensor
            The loss value
        """
        raise FaceswapError("GMSD Loss is not currently compatible with PlaidML. Please select a "
                            "different Loss method.")

        true_edge = self._scharr_edges(y_true, True)
        pred_edge = self._scharr_edges(y_pred, True)
        ephsilon = 0.0025
        upper = 2.0 * true_edge * pred_edge
        lower = K.square(true_edge) + K.square(pred_edge)
        gms = (upper + ephsilon) / (lower + ephsilon)
        gmsd = K.std(gms, axis=(1, 2, 3), keepdims=True)
        gmsd = K.squeeze(gmsd, axis=-1)
        return gmsd

    @classmethod
    def _scharr_edges(cls, image, magnitude):
        """ Returns a tensor holding modified Scharr edge maps.

        Parameters
        ----------
        image: tensor
            Image tensor with shape [batch_size, h, w, d] and type float32. The image(s) must be
            2x2 or larger.
        magnitude: bool
            Boolean to determine if the edge magnitude or edge direction is returned

        Returns
        -------
        tensor
            Tensor holding edge maps for each channel. Returns a tensor with shape `[batch_size, h,
            w, d, 2]` where the last two dimensions hold `[[dy[0], dx[0]], [dy[1], dx[1]], ...,
            [dy[d-1], dx[d-1]]]` calculated using the Scharr filter.
        """

        # Define vertical and horizontal Scharr filters.
        # TODO PlaidML: AttributeError: 'Value' object has no attribute 'get_shape'
        static_image_shape = image.get_shape()
        image_shape = K.shape(image)

        # 5x5 modified Scharr kernel ( reshape to (5,5,1,2) )
        matrix = np.array([[[[0.00070, 0.00070]],
                            [[0.00520, 0.00370]],
                            [[0.03700, 0.00000]],
                            [[0.00520, -0.0037]],
                            [[0.00070, -0.0007]]],
                           [[[0.00370, 0.00520]],
                            [[0.11870, 0.11870]],
                            [[0.25890, 0.00000]],
                            [[0.11870, -0.1187]],
                            [[0.00370, -0.0052]]],
                           [[[0.00000, 0.03700]],
                            [[0.00000, 0.25890]],
                            [[0.00000, 0.00000]],
                            [[0.00000, -0.2589]],
                            [[0.00000, -0.0370]]],
                           [[[-0.0037, 0.00520]],
                            [[-0.1187, 0.11870]],
                            [[-0.2589, 0.00000]],
                            [[-0.1187, -0.1187]],
                            [[-0.0037, -0.0052]]],
                           [[[-0.0007, 0.00070]],
                            [[-0.0052, 0.00370]],
                            [[-0.0370, 0.00000]],
                            [[-0.0052, -0.0037]],
                            [[-0.0007, -0.0007]]]])
        num_kernels = [2]
        kernels = K.constant(matrix, dtype='float32')
        kernels = K.tile(kernels, [1, 1, image_shape[-1], 1])

        # Use depth-wise convolution to calculate edge maps per channel.
        # Output tensor has shape [batch_size, h, w, d * num_kernels].
        pad_sizes = [[0, 0], [2, 2], [2, 2], [0, 0]]
        padded = pad(image, pad_sizes, mode='REFLECT')
        output = K.depthwise_conv2d(padded, kernels)

        if not magnitude:  # direction of edges
            # Reshape to [batch_size, h, w, d, num_kernels].
            shape = K.concatenate([image_shape, num_kernels], axis=0)
            output = K.reshape(output, shape=shape)
            output.set_shape(static_image_shape.concatenate(num_kernels))
            output = tf.atan(K.squeeze(output[:, :, :, :, 0] / output[:, :, :, :, 1], axis=None))
        # magnitude of edges -- unified x & y edges don't work well with Neural Networks
        return output


class LossWrapper():  # pylint:disable=too-few-public-methods
    """ A wrapper class for multiple keras losses to enable multiple weighted loss functions on a
    single output and masking.
    """
    def __init__(self):
        logger.debug("Initializing: %s", self.__class__.__name__)
        self._loss_functions = []
        self._loss_weights = []
        self._mask_channels = []
        logger.debug("Initialized: %s", self.__class__.__name__)

    def add_loss(self, function, weight=1.0, mask_channel=-1):
        """ Add the given loss function with the given weight to the loss function chain.

        Parameters
        ----------
        function: :class:`keras.losses.Loss`
            The loss function to add to the loss chain
        weight: float, optional
            The weighting to apply to the loss function. Default: `1.0`
        mask_channel: int, optional
            The channel in the `y_true` image that the mask exists in. Set to `-1` if there is no
            mask for the given loss function. Default: `-1`
        """
        logger.debug("Adding loss: (function: %s, weight: %s, mask_channel: %s)",
                     function, weight, mask_channel)
        self._loss_functions.append(function)
        self._loss_weights.append(weight)
        self._mask_channels.append(mask_channel)

    def __call__(self, y_true, y_pred):
        """ Call the sub loss functions for the loss wrapper.

        Weights are returned as the weighted sum of the chosen losses.

        Parameters
        ----------
        y_true: tensor or variable
            The ground truth value
        y_pred: tensor or variable
            The predicted value

        Returns
        -------
        tensor
            The final loss value
        """
        loss = 0.0
        for func, weight, mask_channel in zip(self._loss_functions,
                                              self._loss_weights,
                                              self._mask_channels):
            logger.debug("Processing loss function: (func: %s, weight: %s, mask_channel: %s)",
                         func, weight, mask_channel)
            n_true, n_pred = self._apply_mask(y_true, y_pred, mask_channel)
            if isinstance(func, DSSIMObjective):
                # Extract Image Patches in SSIM requires that y_pred be of a known shape, so
                # specifically reshape the tensor.
                n_pred = K.reshape(n_pred, K.int_shape(y_pred))
            this_loss = func(n_true, n_pred)
            loss_dims = K.ndim(this_loss)
            loss += (K.mean(this_loss, axis=list(range(1, loss_dims))) * weight)
        return loss

    @classmethod
    def _apply_mask(cls, y_true, y_pred, mask_channel, mask_prop=1.0):
        """ Apply the mask to the input y_true and y_pred. If a mask is not required then
        return the unmasked inputs.

        Parameters
        ----------
        y_true: tensor or variable
            The ground truth value
        y_pred: tensor or variable
            The predicted value
        mask_channel: int
            The channel within y_true that the required mask resides in
        mask_prop: float, optional
            The amount of mask propagation. Default: `1.0`

        Returns
        -------
        tuple
            (n_true, n_pred): The ground truth and predicted value tensors with the mask applied
        """
        if mask_channel == -1:
            logger.debug("No mask to apply")
            return y_true[..., :3], y_pred[..., :3]

        logger.debug("Applying mask from channel %s", mask_channel)

        mask = K.tile(K.expand_dims(y_true[..., mask_channel], axis=-1), (1, 1, 1, 3))
        mask_as_k_inv_prop = 1 - mask_prop
        mask = (mask * mask_prop) + mask_as_k_inv_prop

        m_true = y_true[..., :3] * mask
        m_pred = y_pred[..., :3] * mask

        return m_true, m_pred

from tqdm import tqdm
from math import ceil
from keras.models import model_from_json
import numpy as np
from keras.activations import elu
import cv2
import time
import scipy as sp
import numpy as np
import pandas as pd
from tqdm import tqdm
from PIL import Image
from functools import partial
import matplotlib.pyplot as plt

import tensorflow as tf
import keras
from keras import initializers
from keras import regularizers
from keras import constraints
from keras import backend as K
from keras.activations import elu
from keras.optimizers import Adam
from keras.models import Sequential
from keras.engine import Layer, InputSpec
from keras.utils.generic_utils import get_custom_objects
from keras.callbacks import Callback, EarlyStopping, ReduceLROnPlateau
from keras.layers import Dense, Conv2D, Flatten, GlobalAveragePooling2D, Dropout
from keras.preprocessing.image import ImageDataGenerator
from sklearn.metrics import cohen_kappa_score
from keras.models import model_from_json
import efficientnet.keras as efn 
class GroupNormalization(Layer):
    """Group normalization layer
    Group Normalization divides the channels into groups and computes within each group
    the mean and variance for normalization. GN's computation is independent of batch sizes,
    and its accuracy is stable in a wide range of batch sizes
    # Arguments
        groups: Integer, the number of groups for Group Normalization.
        axis: Integer, the axis that should be normalized
            (typically the features axis).
            For instance, after a `Conv2D` layer with
            `data_format="channels_first"`,
            set `axis=1` in `BatchNormalization`.
        epsilon: Small float added to variance to avoid dividing by zero.
        center: If True, add offset of `beta` to normalized tensor.
            If False, `beta` is ignored.
        scale: If True, multiply by `gamma`.
            If False, `gamma` is not used.
            When the next layer is linear (also e.g. `nn.relu`),
            this can be disabled since the scaling
            will be done by the next layer.
        beta_initializer: Initializer for the beta weight.
        gamma_initializer: Initializer for the gamma weight.
        beta_regularizer: Optional regularizer for the beta weight.
        gamma_regularizer: Optional regularizer for the gamma weight.
        beta_constraint: Optional constraint for the beta weight.
        gamma_constraint: Optional constraint for the gamma weight.
    # Input shape
        Arbitrary. Use the keyword argument `input_shape`
        (tuple of integers, does not include the samples axis)
        when using this layer as the first layer in a model.
    # Output shape
        Same shape as input.
    # References
        - [Group Normalization](https://arxiv.org/abs/1803.08494)
    """

    def __init__(self,
                 groups=32,
                 axis=-1,
                 epsilon=1e-5,
                 center=True,
                 scale=True,
                 beta_initializer='zeros',
                 gamma_initializer='ones',
                 beta_regularizer=None,
                 gamma_regularizer=None,
                 beta_constraint=None,
                 gamma_constraint=None,
                 **kwargs):
        super(GroupNormalization, self).__init__(**kwargs)
        self.supports_masking = True
        self.groups = groups
        self.axis = axis
        self.epsilon = epsilon
        self.center = center
        self.scale = scale
        self.beta_initializer = initializers.get(beta_initializer)
        self.gamma_initializer = initializers.get(gamma_initializer)
        self.beta_regularizer = regularizers.get(beta_regularizer)
        self.gamma_regularizer = regularizers.get(gamma_regularizer)
        self.beta_constraint = constraints.get(beta_constraint)
        self.gamma_constraint = constraints.get(gamma_constraint)

    def build(self, input_shape):
        dim = input_shape[self.axis]

        if dim is None:
            raise ValueError('Axis ' + str(self.axis) + ' of '
                             'input tensor should have a defined dimension '
                             'but the layer received an input with shape ' +
                             str(input_shape) + '.')

        if dim < self.groups:
            raise ValueError('Number of groups (' + str(self.groups) + ') cannot be '
                             'more than the number of channels (' +
                             str(dim) + ').')

        if dim % self.groups != 0:
            raise ValueError('Number of groups (' + str(self.groups) + ') must be a '
                             'multiple of the number of channels (' +
                             str(dim) + ').')

        self.input_spec = InputSpec(ndim=len(input_shape),
                                    axes={self.axis: dim})
        shape = (dim,)

        if self.scale:
            self.gamma = self.add_weight(shape=shape,
                                         name='gamma',
                                         initializer=self.gamma_initializer,
                                         regularizer=self.gamma_regularizer,
                                         constraint=self.gamma_constraint)
        else:
            self.gamma = None
        if self.center:
            self.beta = self.add_weight(shape=shape,
                                        name='beta',
                                        initializer=self.beta_initializer,
                                        regularizer=self.beta_regularizer,
                                        constraint=self.beta_constraint)
        else:
            self.beta = None
        self.built = True

    def call(self, inputs, **kwargs):
        input_shape = K.int_shape(inputs)
        tensor_input_shape = K.shape(inputs)

        # Prepare broadcasting shape.
        reduction_axes = list(range(len(input_shape)))
        del reduction_axes[self.axis]
        broadcast_shape = [1] * len(input_shape)
        broadcast_shape[self.axis] = input_shape[self.axis] // self.groups
        broadcast_shape.insert(1, self.groups)

        reshape_group_shape = K.shape(inputs)
        group_axes = [reshape_group_shape[i] for i in range(len(input_shape))]
        group_axes[self.axis] = input_shape[self.axis] // self.groups
        group_axes.insert(1, self.groups)

        # reshape inputs to new group shape
        group_shape = [group_axes[0], self.groups] + group_axes[2:]
        group_shape = K.stack(group_shape)
        inputs = K.reshape(inputs, group_shape)

        group_reduction_axes = list(range(len(group_axes)))
        group_reduction_axes = group_reduction_axes[2:]

        mean = K.mean(inputs, axis=group_reduction_axes, keepdims=True)
        variance = K.var(inputs, axis=group_reduction_axes, keepdims=True)

        inputs = (inputs - mean) / (K.sqrt(variance + self.epsilon))

        # prepare broadcast shape
        inputs = K.reshape(inputs, group_shape)
        outputs = inputs

        # In this case we must explicitly broadcast all parameters.
        if self.scale:
            broadcast_gamma = K.reshape(self.gamma, broadcast_shape)
            outputs = outputs * broadcast_gamma

        if self.center:
            broadcast_beta = K.reshape(self.beta, broadcast_shape)
            outputs = outputs + broadcast_beta

        outputs = K.reshape(outputs, tensor_input_shape)

        return outputs

    def get_config(self):
        config = {
            'groups': self.groups,
            'axis': self.axis,
            'epsilon': self.epsilon,
            'center': self.center,
            'scale': self.scale,
            'beta_initializer': initializers.serialize(self.beta_initializer),
            'gamma_initializer': initializers.serialize(self.gamma_initializer),
            'beta_regularizer': regularizers.serialize(self.beta_regularizer),
            'gamma_regularizer': regularizers.serialize(self.gamma_regularizer),
            'beta_constraint': constraints.serialize(self.beta_constraint),
            'gamma_constraint': constraints.serialize(self.gamma_constraint)
        }
        base_config = super(GroupNormalization, self).get_config()
        return dict(list(base_config.items()) + list(config.items()))

    def compute_output_shape(self, input_shape):
        return input_shape

def crop_image_from_gray(img, tol=7):
    """
    Applies masks to the orignal image and 
    returns the a preprocessed image with 
    3 channels
    """
    # If for some reason we only have two channels
    if img.ndim == 2:
        mask = img > tol
        return img[np.ix_(mask.any(1),mask.any(0))]
    # If we have a normal RGB images
    elif img.ndim == 3:
        gray_img = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        mask = gray_img > tol
        
        check_shape = img[:,:,0][np.ix_(mask.any(1),mask.any(0))].shape[0]
        if (check_shape == 0): # image is too dark so that we crop out everything,
            return img # return original image
        else:
            img1=img[:,:,0][np.ix_(mask.any(1),mask.any(0))]
            img2=img[:,:,1][np.ix_(mask.any(1),mask.any(0))]
            img3=img[:,:,2][np.ix_(mask.any(1),mask.any(0))]
            img = np.stack([img1,img2,img3],axis=-1)
        return img
def preprocess_image(image, sigmaX=10):
    """
    The whole preprocessing pipeline:
    1. Read in image
    2. Apply masks
    3. Resize image to desired size
    4. Add Gaussian noise to increase Robustness
    """
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image = crop_image_from_gray(image)
    image = cv2.resize(image, (IMG_WIDTH, IMG_HEIGHT))
    image = cv2.addWeighted (image,4, cv2.GaussianBlur(image, (0,0) ,sigmaX), -4, 128)
    return image
    
exp_name ='exp_100'
SEED = 123456
test_images_path  = 'extra_data/odir/ODIR-5K_Testing_Images/'
json_file = 'saved_models/exp_3/exp_7model_ef5fn_on_dr.json'
weights_path="saved_models/exp_3/exp_7wieghts_ef5dr_fn.h5"
sample_subm_path  = 'extra_data/odir/XYZ_ODIR.csv'
json_file = open(json_file, 'r')
loaded_model_json = json_file.read()
model = model_from_json(loaded_model_json)
# load weights into new model
model.load_weights(weights_path)
print("Loaded model from disk")
###
json_file2 = 'saved_models/exp_5/exp_5_model.json'
weights_path2="saved_models/exp_5/exp_5_wieghts.h5"
json_file2 = open(json_file2, 'r')
loaded_model_json = json_file2.read()
model2 = model_from_json(loaded_model_json)
# load weights into new model
model2.load_weights(weights_path2)
####
json_file3 = 'saved_models/exp_11/exp_11_model.json'
weights_path3="saved_models/exp_11/exp_11_wieghts.h5"
json_file3 = open(json_file3, 'r')
loaded_model_json = json_file3.read()
model3 = model_from_json(loaded_model_json)
# load weights into new model
model3.load_weights(weights_path3)
print("Loaded model from disk")
###
# json_file4 = 'saved_models/exp_12_old/exp_11model_ef5fn_on_dr.json'
# weights_path4="saved_models/exp_12_old/exp_11wieghts_ef5dr_fn.h5"
# json_file4 = open(json_file4, 'r')
# loaded_model_json = json_file4.read()
# model4 = model_from_json(loaded_model_json)
# # load weights into new model
# model4.load_weights(weights_path4)
# print("Loaded model from disk")
###
df_sample = pd.read_csv(sample_subm_path)
df_test_left = pd.DataFrame() 
df_test_left['id']     = df_sample.ID
df_test_left['pic_id'] = df_sample.ID.apply(lambda x: str(x)+"_left.jpg")
df_test_left.to_csv('extra_data/odir/test_df_left.csv')

df_test_right = pd.DataFrame() 
df_test_right['id']     = df_sample.ID
df_test_right['pic_id'] = df_sample.ID.apply(lambda x: str(x)+"_right.jpg")
df_test_right.to_csv('extra_data/odir/train_df_right.csv')

print('DF were created.')

BATCH_SIZE =3
IMG_WIDTH  = 512
IMG_HEIGHT = 512
# Add Image augmentation to our generator
train_datagen = ImageDataGenerator(rotation_range=360,
                                   horizontal_flip=True,
                                   vertical_flip=True,
                                   validation_split=0.15,
                                   preprocessing_function=preprocess_image, 
                                   rescale=1 / 128.)

### -------- left -------------------------------------
test_generator=train_datagen.flow_from_dataframe(dataframe=df_test_left, 
                                                directory = test_images_path,
                                                x_col="pic_id",
                                                target_size=(IMG_WIDTH, IMG_HEIGHT),
                                                batch_size=1,
                                                shuffle=False, 
                                                class_mode=None, seed=SEED)
# -----------------------------------------------------  
preds_tta_1=[]
preds_tta_2=[]
preds_tta_3=[]
for i in tqdm(range(10)):
    test_generator.reset()
    preds = model.predict_generator(generator=test_generator,steps = ceil(df_test_left.shape[0]))
    preds2 = model2.predict_generator(generator=test_generator,steps = ceil(df_test_left.shape[0]))
    preds3 = model3.predict_generator(generator=test_generator,steps = ceil(df_test_left.shape[0]))
    preds_tta_1.append(preds)
    preds_tta_2.append(preds2)
    preds_tta_3.append(preds3)
# -----------------------------------------------------  
pred_left_1 = np.mean(preds_tta_1, axis=0)    
pred_left_2 = np.mean(preds_tta_2, axis=0)    
pred_left_3 = np.mean(preds_tta_3, axis=0)    
    
### -------- rigth ------------------------------------
test_generator=train_datagen.flow_from_dataframe(dataframe=df_test_right, 
                                                directory = test_images_path,
                                                x_col="pic_id",
                                                target_size=(IMG_WIDTH, IMG_HEIGHT),
                                                batch_size=1,
                                                shuffle=False, 
                                                class_mode=None, seed=SEED)
# -----------------------------------------------------  
preds_tta_1=[]
preds_tta_2=[]
preds_tta_3=[]
for i in tqdm(range(10)):
    test_generator.reset()
    preds = model.predict_generator(generator=test_generator,steps = ceil(df_test_right.shape[0]))
    preds2 = model2.predict_generator(generator=test_generator,steps = ceil(df_test_left.shape[0]))
    preds3 = model3.predict_generator(generator=test_generator,steps = ceil(df_test_left.shape[0]))
    preds_tta_1.append(preds)
    preds_tta_2.append(preds2)
    preds_tta_3.append(preds3)
# -----------------------------------------------------  
pred_right_1 = np.mean(preds_tta_1, axis=0)    
pred_right_2 = np.mean(preds_tta_2, axis=0)    
pred_right_3 = np.mean(preds_tta_3, axis=0)    

final_predict_1 = 0.5*pred_left_1+0.5*pred_right_1
final_predict_2 = 0.5*pred_left_2+0.5*pred_right_2
final_predict_3 = 0.5*pred_left_3+0.5*pred_right_3

np.savetxt("exp_3.csv", final_predict_1, delimiter=",")  
np.savetxt("exp_5.csv", final_predict_2, delimiter=",")                                                                                                                                             
np.savetxt("exp_11.csv", final_predict_3, delimiter=",")

final_predict = 0.8*final_predict_1+0.9*final_predict_2+0.7*final_predict_3
# or other way. like 0.8 + 0.1 + 0.1 or etc .. 

df_submit = pd.DataFrame()
df_submit['ID'] = df_test_right.id
df_submit['N'] = final_predict[:,0]
df_submit['D'] = final_predict[:,1]
df_submit['G'] = final_predict[:,2]
df_submit['C'] = final_predict[:,3]
df_submit['A'] = final_predict[:,4]
df_submit['H'] = final_predict[:,5]
df_submit['M'] = final_predict[:,6]
df_submit['O'] = final_predict[:,7]
df_submit.to_csv('Andy_ODIRdr_{}'.format(exp_name),index=False)
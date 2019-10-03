import tensorflow as tf
from tensorflow import keras
from tensorflow.keras.layers import (Conv2D, BatchNormalization, MaxPool2D,
                                     DepthwiseConv2D, Concatenate, Reshape,
                                     Permute, Concatenate, Lambda)

BATCH_NORM_PARAMS = {'decay': 0.997, 'epsilon': 1e-5}
WEIGHT_DECAY = 0.00004


def batch_norm():
    return BatchNormalization(momentum=BATCH_NORM_PARAMS['decay'],
                              epsilon=BATCH_NORM_PARAMS['epsilon'])


def entry_layer(weight_decay: float = WEIGHT_DECAY):
    return keras.Sequential(
        # name='entry_layer',
        layers=[
            Conv2D(24,
                   kernel_size=3,
                   strides=2,
                   padding="same",
                   activation="relu",
                   kernel_regularizer=keras.regularizers.l2(weight_decay)),
            batch_norm(),
            MaxPool2D(padding="same")
        ])


def basic_unit_with_downsampling(in_channels: int,
                                 out_channels: int = None,
                                 stride: int = 2,
                                 rate: int = 1,
                                 weight_decay: float = WEIGHT_DECAY):
    out_channels = 2 * in_channels if out_channels is None else out_channels
    right_path = keras.Sequential(
        # name='downsampling_right_path',
        layers=[
            Conv2D(in_channels,
                   kernel_size=1,
                   strides=1,
                   padding="same",
                   activation="relu",
                   kernel_regularizer=keras.regularizers.l2(weight_decay)),
            batch_norm(),
            DepthwiseConv2D(kernel_size=3,
                            strides=stride,
                            dilation_rate=rate,
                            padding="same"),
            batch_norm(),
            Conv2D(out_channels // 2,
                   kernel_size=1,
                   strides=1,
                   padding="same",
                   activation="relu",
                   kernel_regularizer=keras.regularizers.l2(weight_decay)),
            batch_norm()
        ])

    # left path
    left_path = keras.Sequential(
        # name='downsampling_left_path',
        layers=[
            DepthwiseConv2D(kernel_size=3,
                            strides=stride,
                            dilation_rate=rate,
                            padding="same"),
            batch_norm(),
            Conv2D(out_channels // 2,
                   kernel_size=1,
                   strides=1,
                   padding="same",
                   activation="relu",
                   kernel_regularizer=keras.regularizers.l2(weight_decay)),
            batch_norm()
        ])

    return left_path, right_path


def channel_shuffle(inputs: tf.Tensor):
    _, height, width, depth = inputs.shape

    _x = Reshape([-1, 2, depth // 2])(inputs)
    _x = Permute([1, 3, 2])(_x)
    _x = Reshape([height, width, depth])(_x)

    return _x


def concat_shuffle_split(inputs: list):
    _x = Concatenate()(inputs)
    _x = channel_shuffle(_x)
    left_path, right_path = tf.split(_x, num_or_size_splits=2, axis=3)

    return left_path, right_path


def basic_unit(in_channels: int,
               rate: int = 1,
               weight_decay: float = WEIGHT_DECAY):
    return keras.Sequential(
        # name='basic_unit',
        layers=[
            Conv2D(in_channels,
                   kernel_size=1,
                   strides=1,
                   padding="same",
                   activation="relu",
                   kernel_regularizer=keras.regularizers.l2(weight_decay)),
            batch_norm(),
            DepthwiseConv2D(kernel_size=3,
                            strides=1,
                            dilation_rate=rate,
                            padding="same"),
            batch_norm(),
            Conv2D(in_channels,
                   kernel_size=1,
                   strides=1,
                   padding="same",
                   activation="relu",
                   kernel_regularizer=keras.regularizers.l2(weight_decay)),
            batch_norm()
        ])


def shufflenet_v2_base(inputs: tf.Tensor,
                       depth_multiplier: float,
                       output_stride: int = 32,
                       weight_decay: float = WEIGHT_DECAY,
                       small_backend: bool = False):
    depth_multipliers = {0.5: 48, 1.0: 116, 1.5: 176, 2.0: 224}
    initial_depth = depth_multipliers[depth_multiplier]

    if output_stride < 4:
        raise ValueError("Output stride should be cannot be lower than 4.")

    layer_info = [
        {
            "num_units": 3,
            "out_channels": initial_depth,
            "scope": "stage_2",
            "stride": 2
        },
        {
            "num_units": 7,
            "out_channels": initial_depth * 2,
            "scope": "stage_3",
            "stride": 2
        },
        {
            "num_units": 3,
            "out_channels": (initial_depth * 2) if small_backend else None,
            "scope": "stage_4",
            "stride": 2
        },
    ]

    def stride_handling(stride: int, current_stride: int, current_rate: int,
                        max_stride: int):
        if current_stride == max_stride:
            return 1, current_rate * stride
        else:
            current_stride *= stride
            return stride, current_rate

    brach_exits = {}

    with tf.name_scope("shufflenet_v2"):
        _x = entry_layer(weight_decay)(inputs)

        current_stride = 4
        current_rate = 1
        brach_exits[str(current_stride)] = _x
        for i in range(3):
            layer = layer_info[i]
            old_rate = current_rate
            stride, rate = stride_handling(layer["stride"], current_stride,
                                           current_rate, output_stride)

            with tf.name_scope(layer["scope"]):
                left_path_model, right_path_model = basic_unit_with_downsampling(
                    _x.shape[-1],
                    layer["out_channels"],
                    stride=stride,
                    rate=rate if rate == old_rate else old_rate,
                    weight_decay=weight_decay)
                left_path = left_path_model(_x)
                right_path = right_path_model(_x)

                for _ in range(layer["num_units"]):
                    left_path, right_path = concat_shuffle_split(
                        [left_path, right_path])
                    left_path = basic_unit(left_path.shape[-1],
                                           rate=rate,
                                           weight_decay=weight_decay)(left_path)
                _x = Concatenate()([left_path, right_path])

            current_stride *= stride
            current_rate *= rate

            if stride != 1:
                brach_exits[str(current_stride)] = _x

    return _x, brach_exits


def shufflenet_v2(inputs: tf.Tensor,
                  num_classes: int,
                  depth_multiplier: float = 1.0,
                  output_stride: int = 32,
                  weight_decay: float = WEIGHT_DECAY):
    from tensorflow.keras import layers

    _x, _ = shufflenet_v2_base(inputs, depth_multiplier, output_stride)

    final_channels = 1024 if depth_multiplier != "2.0" else 2048

    with tf.name_scope("shufflenet_v2/logits"):
        _x = Conv2D(final_channels,
                    kernel_size=1,
                    strides=1,
                    padding="same",
                    kernel_regularizer=keras.regularizers.l2(weight_decay))(_x)
        _x = layers.GlobalAveragePooling2D()(_x)
        _x = layers.Dense(num_classes,
                          activation="softmax",
                          kernel_initializer="he_normal")(_x)

    return _x


if __name__ == "__main__":
    # tf.random.set_seed(22)

    # # The data, split between train and test sets:
    # (x_train, y_train), (x_test, y_test) = keras.datasets.cifar10.load_data()
    # print("x_train shape:", x_train.shape)
    # print(x_train.shape[0], "train samples")
    # print(x_test.shape[0], "test samples")

    # # Convert class vectors to binary class matrices.
    # y_train = keras.utils.to_categorical(y_train, 10)
    # y_test = keras.utils.to_categorical(y_test, 10)

    # x_train = x_train.astype("float32")
    # x_test = x_test.astype("float32")
    # x_train /= 255
    # x_test /= 255

    # input_shape = (32, 32, 3)
    # inputs = keras.Input(shape=input_shape)

    # output = shufflenet_v2(inputs, 10, 1.0)
    # model = tf.keras.Model(inputs=inputs, outputs=output)

    # model.compile(loss="categorical_crossentropy", optimizer="adam", metrics=["accuracy"])

    # model.fit(
    #     x_train,
    #     y_train,
    #     batch_size=128,
    #     epochs=10,
    #     validation_data=(x_test, y_test),
    #     shuffle=True,
    # )

    # scores = model.evaluate(x_test, y_test, verbose=1)
    # print("Test loss:", scores[0])
    # print("Test accuracy:", scores[1])

    # model.save("model.h5")

    inputs = keras.Input(shape=(32, 32, 3))
    output = shufflenet_v2_base(inputs, 1.0, 16)

    model = keras.Model(inputs=inputs, outputs=output)
    # model.load_weights('./checkpoints/cifar10.h5')

    model.summary()
    model.save('./checkpoints/sufflenetv2_base.h5')

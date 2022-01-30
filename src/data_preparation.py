#!/usr/bin/python3

import os
import glob
import shutil

import numpy as np

from osgeo import gdal

from cnn_exceptions import DatasetError


def read_images(data_dir, tensor_shape=(256, 256),
                filter_by_class=None, augment=True, verbose=1):
    """Read images and return them as tensors and lists of filenames.

    TODO: Rewrite into a form of a generator

    :param data_dir: path to the directory containing images
    :param tensor_shape: shape of the first two dimensions of input tensors
    :param verbose: verbosity (0=quiet, >0 verbose)
    :param filter_by_class: classes of interest (if specified, only samples
        containing at least one of them will be created)
    :param augment: boolean saying whether to augment the dataset or not
    :return: image_tensors, masks_tensors
    """
    images_arrays = []
    masks_arrays = []
    for i in sorted(glob.glob(os.path.join(data_dir, '*image.tif'))):
        tiled = tile(i, i.replace('image.tif', 'label.tif'),
                     tensor_shape, filter_by_class, augment)
        images_arrays.extend(tiled[0])
        masks_arrays.extend(tiled[1])

    if len(images_arrays) == 0:
        raise DatasetError('No training samples created. Check the size of '
                           'the images in the data_dir or the appearance of '
                           'the classes you are interested in in labels')

    if masks_arrays[0].ndim == 2:
        masks_arrays = [np.expand_dims(i, -1) for i in masks_arrays]

    im_nr = len(images_arrays)
    if verbose > 0:
        print('Created {} training samples from the provided '
              'image.'.format(im_nr))

    return images_arrays, masks_arrays


def generate_dataset_structure(data_dir, nr_bands=12, tensor_shape=(256, 256),
                               val_set_pct=0.2, filter_by_class=None,
                               augment=True, verbose=1):
    """Generate the expected dataset structure.

    Will generate directories train_images, train_masks, val_images and
    val_masks.

    :param data_dir: path to the directory containing images
    :param nr_bands: number of bands of intended input images
    :param tensor_shape: shape of the first two dimensions of input tensors
    :param val_set_pct: percentage of the validation images in the dataset
    :param filter_by_class: classes of interest (if specified, only samples
        containing at least one of them will be created)
    :param augment: boolean saying whether to augment the dataset or not
    :param verbose: verbosity (0=quiet, >0 verbose)
    """
    # function to be used while saving samples
    def train_val_determination(pct):
        """Return decision about the sample will be part of train or val set.

        :param pct: Percentage at which a val determinator is returned
        """
        cur_pct = 0
        while True:
            cur_pct += pct
            if cur_pct < 1:
                yield 'train'
            else:
                cur_pct -= 1
                yield 'val'

    # Create folders to hold images and masks
    dirs = ['train_images', 'train_masks', 'val_images', 'val_masks']

    for directory in dirs:
        dir_full_path = os.path.join(data_dir, directory)
        if os.path.isdir(dir_full_path):
            shutil.rmtree(dir_full_path)

        os.makedirs(dir_full_path)

    images, masks = read_images(data_dir, tensor_shape, filter_by_class,
                                augment)

    driver = gdal.GetDriverByName('GTiff')
    desired_dtype = np.int16
    desired_dtype_max = np.iinfo(desired_dtype).max
    current_dtype_max = np.iinfo(images[0].dtype).max

    # Iterate over the images while saving the images and masks
    # in appropriate folders
    im_id = 0
    dir_names = train_val_determination(val_set_pct)
    for image, mask in zip(images, masks):
        # Convert tensors to numpy arrays
        # scale to 0-1
        image = image / current_dtype_max
        # scale and convert to the desired dtype
        image = (image * desired_dtype_max).astype(desired_dtype)
        mask = mask.astype(desired_dtype)

        # TODO: Avoid two transpositions
        image = np.transpose(image, (2, 0, 1))
        mask = np.transpose(mask, (2, 0, 1))
        # TODO: https://stackoverflow.com/questions/53776506/how-to-save-an-array-representing-an-image-with-40-band-to-a-tif-file

        dir_name = next(dir_names)
        image_path = os.path.join(data_dir,
                                  '{}_images'.format(dir_name),
                                  'image_{0:03d}.tif'.format(im_id + 1))
        mask_path = os.path.join(data_dir,
                                 '{}_masks'.format(dir_name),
                                 'image_{0:03d}.tif'.format(im_id + 1))

        # write rasters
        dout = driver.Create(image_path, tensor_shape[0],
                             tensor_shape[1], nr_bands, gdal.GDT_UInt16)
        for i in range(nr_bands):
            dout.GetRasterBand(i + 1).WriteArray(image[i])

        dout = driver.Create(mask_path, tensor_shape[0],
                             tensor_shape[1], 1, gdal.GDT_UInt16)
        for i in range(1):
            dout.GetRasterBand(i + 1).WriteArray(mask[i])

        im_id += 1

    if verbose > 0:
        print("Saved {} images to directory {}".format(im_id, data_dir))


def tile(scene_path, labels_path, tensor_shape, filter_by_class=None,
         augment=True):
    """Tile the big scene into smaller samples.

    If filter_by_class is not None, only samples containing at least one of
    these classes of interest will be returned.

    :param scene_path: path to the image to be cut
    :param labels_path: path to the image with labels to be cut
    :param tensor_shape: shape of the first two dimensions of input tensors
    :param filter_by_class: classes of interest (if specified, only samples
        containing at least one of them will be returned)
    :param augment: boolean saying whether to augment the dataset or not
    :return: TODO
    """
    import pyjeo as pj

    # do we filter by classes?
    if filter_by_class is None:
        filt = False
    else:
        filter_by_class = [int(i) for i in filter_by_class.split(',')]
        filt = True

    scene_nps = []
    labels_nps = []

    # load images
    scene = pj.Jim(scene_path)
    labels = pj.Jim(labels_path)

    nr_col = scene.properties.nrOfCol()
    nr_row = scene.properties.nrOfRow()
    cols_step = tensor_shape[0]
    rows_step = tensor_shape[1]

    for i in range(0, nr_col, cols_step):
        for j in range(0, nr_row, rows_step):
            # if reaching the end of the image, expand the window back to
            # avoid pixels outside the image
            if j + rows_step > nr_row:
                j = nr_row - rows_step
            if i + cols_step > nr_col:
                i = nr_col - cols_step

            # crop labels
            labels_cropped = pj.geometry.crop(labels, ulx=i, uly=j,
                                              lrx=i + cols_step,
                                              lry=j + rows_step,
                                              nogeo=True)

            if filt is False or \
                    any(i in labels_cropped.np() for i in filter_by_class):
                # crop image
                scene_cropped = pj.geometry.crop(scene, ulx=i, uly=j,
                                                 lrx=i + cols_step,
                                                 lry=j + rows_step,
                                                 nogeo=True)
                # stack bands
                scene_np = np.stack(
                    [scene_cropped.np(i) for i in
                     range(scene_cropped.properties.nrOfBand())],
                    axis=2)
                labels_np = pj.jim2np(labels_cropped)

                scene_nps.append(scene_np)
                labels_nps.append(labels_np)

    if augment is True:
        for i in range(len(scene_nps)):
            for rot_k in range(1, 4):
                scene_nps.append(np.rot90(scene_nps[i], rot_k))
                labels_nps.append(np.rot90(labels_nps[i], rot_k))

    return scene_nps, labels_nps

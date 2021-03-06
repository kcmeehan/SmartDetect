import streamlit as st
import pickle
import matplotlib.pyplot as plt
from PIL import Image
import sys
import requests
import numpy as np

from mmdet.apis import init_dist, init_detector, inference_detector, show_result
import mmcv
import os
import os.path as osp 
import shutil
import tempfile

import torch
import torch.distributed as dist
from mmcv.parallel import MMDataParallel, MMDistributedDataParallel
from mmcv.runner import get_dist_info, load_checkpoint

from mmdet.core import coco_eval, results2json, wrap_fp16_model
from mmdet.datasets import build_dataloader, build_dataset
from mmdet.models import build_detector

cdir = os.getcwd()
yolo_module_path = os.path.join(cdir, '../PyTorch-YOLOv3/')
reppoints_module_path = os.path.join(cdir, './mmdetection/')
sys.path.append(yolo_module_path)
sys.path.append(reppoints_module_path)
from yolov3_detect import yolov3_detect
from tools.test import single_gpu_test


def test(config_file, checkpoint_file, results_file):
    
    cfg = mmcv.Config.fromfile(config_file)
    # set cudnn_benchmark
    if cfg.get('cudnn_benchmark', False):
        torch.backends.cudnn.benchmark = True
    cfg.model.pretrained = None
    cfg.data.test.test_mode = True

    # init distributed env first, since logger depends on the dist info.
    launcher = 'none'
    if launcher == 'none':
        distributed = False
    else:
        distributed = True
        init_dist(launcher, **cfg.dist_params)

    # build the dataloader
    # TODO: support multiple images per gpu (only minor changes are needed)
    dataset = build_dataset(cfg.data.test)
    data_loader = build_dataloader(
        dataset,
        imgs_per_gpu=1,
        workers_per_gpu=cfg.data.workers_per_gpu,
        dist=distributed,
        shuffle=False)

    # build the model and load checkpoint
    model = build_detector(cfg.model, train_cfg=None, test_cfg=cfg.test_cfg)
    fp16_cfg = cfg.get('fp16', None)
    if fp16_cfg is not None:
        wrap_fp16_model(model)
    checkpoint = load_checkpoint(model, checkpoint_file, map_location='cpu')
    # old versions did not save class info in checkpoints, this walkaround is
    # for backward compatibility
    if 'CLASSES' in checkpoint['meta']:
        model.CLASSES = checkpoint['meta']['CLASSES']
    else:
        model.CLASSES = dataset.CLASSES

    if not distributed:
        model = MMDataParallel(model, device_ids=[0])
        outputs, inference_time = single_gpu_test(model, data_loader, False)
    else:
        print("ERROR: This part needs to be fixed")
        pass
        #model = MMDistributedDataParallel(model.cuda())
        #outputs = multi_gpu_test(model, data_loader, tmpdir)

    rank, _ = get_dist_info()
    if rank == 0:
        print('\nwriting results to {}'.format(results_file))
        mmcv.dump(outputs, results_file)
        eval_types = None
        if eval_types:
            print('Starting evaluate {}'.format(' and '.join(eval_types)))
            if eval_types == ['proposal_fast']:
                result_file = results_file
                coco_eval(result_file, eval_types, dataset.coco)
            else:
                if not isinstance(outputs[0], dict):
                    result_files = results2json(dataset, outputs, results_file)
                    coco_eval(result_files, eval_types, dataset.coco)
                else:
                    for name in outputs[0]:
                        print('\nEvaluating {}'.format(name))
                        outputs_ = [out[name] for out in outputs]
                        result_file = results_file + '.{}'.format(name)
                        result_files = results2json(dataset, outputs_,
                                                    result_file)
                        coco_eval(result_files, eval_types, dataset.coco)
    return dataset.CLASSES, inference_time

def show_result_pyplot(img,
                       result,
                       class_names,
                       score_thr=0.5,
                       fig_size=(15, 10)):
    """Visualize the detection results on the image.
    Args:
        img (str or np.ndarray): Image filename or loaded image.
        result (tuple[list] or list): The detection result, can be either
            (bbox, segm) or just bbox.
        class_names (list[str] or tuple[str]): A list of class names.
        score_thr (float): The threshold to visualize the bboxes and masks.
        fig_size (tuple): Figure size of the pyplot figure.
        out_file (str, optional): If specified, the visualization result will
            be written to the out file instead of shown in a window.
    """
    img = show_result(
        img, result, class_names, score_thr=score_thr, show=False)
    plt.figure(figsize=fig_size)
    st.image(mmcv.bgr2rgb(img))

#---------------------------------------------------------------------
#--------------------STREAMLIT APP------------------------------------
#---------------------------------------------------------------------
st.title("SmartDetect ") #\nObject Detection Model Comparison Tool")
st.header("An Object Detection Model Comparison Tool")

# Choose input method
input_method = st.radio(
        "Choose an input image:",
        ('Pre-loaded Image', 'Upload my own image')
    )

## Choose pre-loaded sample image
if input_method == 'Pre-loaded Image':
    image_selected = st.selectbox(
	'Choose input image for inference:',
	('select image', 'kitchen', 'hot dog', 'sports')
    )

    if image_selected == 'select image':
        img = None
        pass

    if image_selected == 'kitchen':
        img = 'data/coco/sample_image_1/000000397133.jpg'
        config_file = 'configs/test_single_image.py'
        image = Image.open(img)
        st.image(image)

    if image_selected == 'hot dog':
        img = 'data/coco/sample_image_2/000000548555.jpg'
        config_file = 'configs/sample2_config.py'
        image = Image.open(img)
        st.image(image)

    if image_selected == 'sports':
        img = 'data/coco/sample_image_3/000000232692.jpg'
        config_file = 'configs/sample3_config.py'
        image = Image.open(img)
        st.image(image)

## Choose to upload image from url
elif input_method == 'Upload my own image':
    default_url = "https://c402277.ssl.cf1.rackcdn.com/photos/18128/images/hero_small/Medium_WW247497.jpg"
    url = st.text_input("Input image url", default_url)

    if url == "":
        url = default_url
        st.write("Warning! No url passed. Using default: ")
        st.write(url)
    img_data = requests.get(url).content

    if not os.path.exists("uploaded_images"):
        os.mkdir("uploaded_images")

    with open('uploaded_images/custom_image.jpg', 'wb') as handler:
        handler.write(img_data)

    img = 'uploaded_images/custom_image.jpg'
    image = Image.open(img)
    st.image(image)
    config_file = 'configs/urltest.py'

# Adds a selectbox to the sidebar
#model_selected = st.sidebar.selectbox(
model_selected = st.selectbox(
    'Choose model for inference:',
    ('select model', 'yolov3', 'RepPoints')
)

if model_selected == 'select model':
    pass

if model_selected == 'yolov3' :
    st.write("Model selected: " + model_selected)
    img_path = os.path.dirname(img) 
    weights_path = "../PyTorch-YOLOv3/weights/yolov3.weights"
    model_def = "../PyTorch-YOLOv3/config/yolov3.cfg"
    class_path = "../PyTorch-YOLOv3/data/coco.names"

    st.write("Running inference...")
    inference_time = yolov3_detect(img_path, weights_path, model_def, class_path)
    st.write("Inference time: " + str(inference_time.total_seconds()) + " seconds on an NVIDIA Tesla K80 gpu")

if model_selected == 'RepPoints':
  
    st.write("Model selected: " + model_selected)
    checkpoint_file = 'checkpoints/reppoints_moment_x101_dcn_fpn_2x_mt.pth'
    results_file = 'results.pkl'

    st.write("Running inference...")
    classes, inference_time = test(config_file, checkpoint_file, results_file)

    st.write("Displaying result...")
    pkl_file = open(results_file, "rb")
    data = pickle.load(pkl_file)

    det = data[0]
    bboxes = np.vstack(det)
    labels = [ 
        np.full(bbox.shape[0], i, dtype=np.int32)
        for i, bbox in enumerate(det)
    ]   
    labels = np.concatenate(labels)
    scores = bboxes[:, -1]
    inds = scores > 0.5
    bboxes = bboxes[inds, :]
    labels = labels[inds]
    
    for bbox, label in zip(bboxes, labels):
        label_text = classes[label]
        st.text("Label: %s, Confidence: %.2f %%" % (label_text, bbox[-1]*100.))
        
    show_result_pyplot(img, data[0], classes)
    st.write("Inference time: " + str(inference_time.total_seconds()) + " seconds on an NVIDIA Tesla K80 gpu")



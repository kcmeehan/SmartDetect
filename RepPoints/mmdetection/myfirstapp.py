import streamlit as st
import pickle
import matplotlib.pyplot as plt

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

def single_gpu_test(model, data_loader, show=False):
    model.eval()
    results = []
    dataset = data_loader.dataset
    prog_bar = mmcv.ProgressBar(len(dataset))
    for i, data in enumerate(data_loader):
        with torch.no_grad():
            result = model(return_loss=False, rescale=not show, **data)
        results.append(result)

        if show:
            model.module.show_result(data, result, dataset.img_norm_cfg)

        batch_size = data['img'][0].size(0)
        for _ in range(batch_size):
            prog_bar.update()
       
    return results

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
        outputs = single_gpu_test(model, data_loader, False)
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
    return dataset.CLASSES

def show_result_pyplot(img,
                       result,
                       class_names,
                       score_thr=0.3,
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

#--------------------STREAMLIT------------------------------------
st.title("Object Detection Model Comparison")

# Adds a selectbox to the sidebar
model_selected = st.sidebar.selectbox(
    'Choose model for inference:',
    ('yolov3', 'RepPoints')
)

if model_selected == 'yolov3' :
    st.write("Model selected: " + model_selected)
    pass

if model_selected == 'RepPoints':
  
    st.write("Model selected: " + model_selected)
    config_file = 'configs/test_single_image.py'
    checkpoint_file = 'checkpoints/reppoints_moment_x101_dcn_fpn_2x_mt.pth'
    results_file = 'results.pkl'
    img = 'data/coco/sample_image_1/000000397133.jpg'

    st.write("Running inference...")
    classes = test(config_file, checkpoint_file, results_file)

    st.write("Displaying result...")
    pkl_file = open(results_file, "rb")
    data = pickle.load(pkl_file)
    #st.write(data)
    show_result_pyplot(img, data[0], classes)

#st.write("Here's our first attempt at using data to create a table:")
#st.write(pd.DataFrame({
#        'first column': [1, 2, 3, 4], 
#            'second column': [10, 20, 30, 50] 
#
#}))


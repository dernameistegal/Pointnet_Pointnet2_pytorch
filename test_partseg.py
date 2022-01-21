import os
import torch
import sys
import importlib
import shutil
import numpy as np
import custom_functions.transform as t
import tqdm
import custom_functions.general_utils as gu
sys.path.append("/content/Pointnet_Pointnet2_pytorch/data_utils")


def evaluate_model(npoints, source_path):

    split_path = source_path + "/split/valsplit.npy"
    valindices = np.load(split_path)
    f1score, precision, recall, total, correct = [], [], [], [], []

    for i in len(valindices):
        pred, allpoints, target = multi_sample_ensemble(source_path, npoints, tree_number=i, n_samples=5)
        pred_choice = (pred > 0.5).astype("int")

        # measures
        tp = np.sum(np.logical_and(target == 1, pred_choice == 1))
        fn = np.sum(np.logical_and(target == 1, pred_choice == 0))
        fp = np.sum(np.logical_and(target == 0, pred_choice == 1))
        tn = np.sum(np.logical_and(target == 0, pred_choice == 0))

        precision.append(tp / (tp + fp))
        recall.append(tp / (tp + fn))
        f1score.append(2 * (precision[-1] * recall[-1]) / (precision[-1] + recall[-1]))
        correct.append(tp + tn)
        total.append(len(pred))

    acc = np.array(correct) / np.array(total)
    print("Acc:", np.sum(correct) / np.sum(total), "F1 score", np.mean(f1score), "Precision:", np.mean(precision),
          "Recall:", np.mean(recall))

    return precision, recall, f1score, acc


def to_categorical(y, num_classes):
    """ 1-hot encodes a tensor """
    new_y = torch.eye(num_classes)[y.cpu().data.numpy(), ]
    if (y.is_cuda):
        return new_y.cuda()
    return new_y

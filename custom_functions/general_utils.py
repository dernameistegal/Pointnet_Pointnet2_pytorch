import torch
import sys
import importlib
import os
from sklearn.neighbors import NearestNeighbors
import transform as t
import ShapeNetDataLoader as dset
import numpy as np
import json
position_path = "/content/drive/MyDrive/Colab/tree_learning/data/positions_attempt2.json"


def get_device(cuda_preference=True):
    print('cuda available:', torch.cuda.is_available(),
          '; cudnn available:', torch.backends.cudnn.is_available(),
          '; num devices:', torch.cuda.device_count())

    use_cuda = False if not cuda_preference else torch.cuda.is_available()
    device = torch.device('cuda:0' if use_cuda else 'cpu')
    device_name = torch.cuda.get_device_name(device) if use_cuda else 'cpu'
    print('Using device', device_name)
    return device


def gen_split(percentages=(0.5, 0.2),
              paths=("/content/Pointnet_Pointnet2_pytorch/data/trainsplit.npy",
                     "/content/Pointnet_Pointnet2_pytorch/data/valsplit.npy"),
              sample_number=252,
              shuffle=True,
              seed=1):
    import random
    import numpy as np
    if shuffle:
        random.seed(seed)
        indices = range(sample_number)
        indices = np.array(random.sample(indices, sample_number))
    else:
        indices = np.arange(0, sample_number)
    start, percentage = 0, 0
    for i, (path) in enumerate(paths):
        percentage += percentages[i]
        stop = np.floor(percentage * sample_number).astype(int)
        index_subset = indices[start:stop]
        np.save(path, index_subset)
        start = stop


def gen_spatial_split(percentages=(0.7, 0.3),
                      paths=("/content/Pointnet_Pointnet2_pytorch/data/trainsplit.npy",
                             "/content/Pointnet_Pointnet2_pytorch/data/valsplit.npy"),
                      position_path=position_path,
                      sample_number=252,
                      shuffle=True,
                      seed=1):
    # constructs a rectangle that encompasses a part of the forest given by percentages
    import random
    import numpy as np
    with open(position_path, "r") as f:
        positions = json.load(f)

    positions = np.array([i[1] for i in positions])
    minval, maxval = np.amin(positions, axis=0)[0:2], np.amax(positions, axis=0)[0:2]
    sidelength = np.sqrt(percentages[1]) * (maxval - minval)
    if shuffle:
        random.seed(seed)
        offset = np.array([random.random(), random.random()])
        offset = offset * (maxval - minval - sidelength)
        isin = np.all(np.logical_and(positions[:, :2] > minval + offset, positions[:, :2] < minval + sidelength + offset), axis=1)
    else:
        isin = np.all(positions[:,:2] < minval[:2] + sidelength, axis=1)
        offset = 0

    indices = np.arange(0, len(positions))
    np.save(paths[1], indices[isin])
    np.save(paths[0], indices[np.invert(isin)])

    return sidelength, offset


def get_model(source_path, device):
    model_name = set(os.listdir(source_path)) - set(
        ["pointnet2_utils.py", "logs", "checkpoints", "performance", "split", "__pycache__"])
    model_name = list(model_name)[0]
    model_name = model_name[0:-3]
    sys.path.append(source_path)
    model = importlib.import_module(model_name)

    def inplace_relu(m):
        classname = m.__class__.__name__
        if classname.find('ReLU') != -1:
            m.inplace = True

    classifier = model.get_model(2, normal_channel=False).to(device)
    classifier.apply(inplace_relu)

    model_path = source_path + "/checkpoints/best_model.pth"
    checkpoint = torch.load(model_path)
    classifier.load_state_dict(checkpoint['model_state_dict'])

    return classifier


def gen_pred(classifier, tree_number, treedataset, device):
    # predict targets for arbitrary tree number
    points, label, target, _, upoints, alltarget = treedataset[tree_number]
    points, label, target = torch.tensor(points), torch.tensor(label), torch.tensor(target)
    points, target = torch.unsqueeze(points, 0), torch.unsqueeze(target, 0)
    points, label, target = points.float().to(device), label.long().to(device), target.long().to(device)
    points = points.transpose(2, 1)

    def to_categorical(y, num_classes):
        """ 1-hot encodes a tensor """
        new_y = torch.eye(num_classes)[y.cpu().data.numpy(),]
        if y.is_cuda:
            return new_y.cuda()
        return new_y

    with torch.no_grad():
        classifier.eval()
        result = classifier(points, to_categorical(label, 1))[0]

    pred_probabilities = torch.exp(result[0])[:, 1].detach().cpu().numpy()

    return pred_probabilities, upoints


def find_neighbours(upoints, allpoints, k):
    nbrs = NearestNeighbors(n_neighbors=k, algorithm='ball_tree').fit(upoints)
    neighbours_indices = nbrs.kneighbors(allpoints, k, return_distance=False)

    return neighbours_indices


def extrapolate(pred_probabilities, neighbours_indices):
    # produce nxk array
    mapped_probabilities = pred_probabilities[neighbours_indices]

    return np.mean(mapped_probabilities, axis=1)


def compute_certainty_score(probability, threshold):

    if (probability - threshold) < 0:
        certainty_score = (probability - threshold) / threshold
    else:
        certainty_score = (probability - threshold) / (1 - threshold)

    return certainty_score


def multi_sample_ensemble(source_path, npoints, tree_number, n_samples=5, method="mean"):
    split_path = source_path + "/split/valsplit.npy"
    root = "/content/Pointnet_Pointnet2_pytorch/data/"

    # if best threshold is available, choose it, otherwise simply use 0.5 as threshold
    try:
        checkpoint = torch.load(source_path + '/checkpoints/best_model.pth')
        best_threshold = checkpoint["best_threshold"]
    except:
        best_threshold = 0.5

    use_cuda = torch.cuda.is_available()
    device = torch.device('cuda:0' if use_cuda else 'cpu')
    classifier = get_model(source_path, device)
    dataset = dset.PartNormalDataset(root=root,
                                     npoints=npoints,
                                     transform=t.Compose([t.Normalize()]),
                                     splitpath=split_path,
                                     normal_channel=False, mode="eval")

    # generate n_samples predictions
    allpoints = dataset[tree_number][3]
    targets = dataset[tree_number][5]
    preds = np.empty((len(allpoints), n_samples))
    for i in range(n_samples):
        pred_probabilities, upoints = gen_pred(classifier, tree_number=tree_number, treedataset=dataset, device=device)
        indices = find_neighbours(upoints, allpoints, 5)
        preds[:, i] = extrapolate(pred_probabilities, indices)

    if method == "mean":
        preds = np.vectorize(compute_certainty_score)(preds, best_threshold)
        preds = preds / 2 + 0.5
        preds = np.mean(preds, axis=1)

    elif method == "majority":
        preds = (preds > best_threshold).astype("int")
        preds = np.mean(preds, axis=1)

    return preds, allpoints, targets, best_threshold


def multi_model_ensemble(source_paths, npoints, tree_number, n_samples=5, method="mean"):

    best_thresholds = []
    preds = []

    for source_path in source_paths:
        pred, allpoints, targets, best_threshold = multi_sample_ensemble(source_path, npoints, tree_number, n_samples, method)
        preds.append(pred)
        best_thresholds.append(best_threshold)

    preds = np.array(preds).T

    if method == "mean":
        preds = np.mean(preds, axis=1)

    elif method == "majority":
        preds = (preds >= 0.5).astype("int")
        preds = np.mean(preds, axis=1)

    return preds, allpoints, targets, best_thresholds


def multi_tree_ensemble(source_paths, npoints, tree_number, radius=10, n_samples=5, method="mean",
                        position_path=position_path):
    # determine tree numbers where a prediction is needed
    with open(position_path, "r") as f:
        positions = json.load(f)
    split = np.load(source_paths[0] + "/split/valsplit.npy")
    old_number = tree_number
    tree_number = split[tree_number]

    positions = np.array([i[1] for i in positions])
    center = positions[tree_number]  # todo verify that this is correct
    distances = np.linalg.norm(positions[:, :2] - center[:2], ord=None, axis=1)
    tree_indices = np.argwhere(distances < radius)
    tree_indices = tree_indices.reshape(len(tree_indices))
    print(tree_indices)

    # only choose tree_indices in valsplit
    ids = []
    for index in tree_indices:
        test = np.argwhere(index == split)
        if len(test) > 0:
            ids.append(test[0, 0])
    print(ids)

    # generate predictions
    all_preds = []
    assert len(ids) > 0
    for tree in ids:
        pred, points = multi_model_ensemble(source_paths, npoints, tree, n_samples, method)[:2]
        if tree == old_number:
            relevant_points = tuple(tuple(x) for x in points.tolist())
            prediction = tuple(x for x in pred.tolist())
        else:
            all_preds.append((points, pred))

    predcollation = dict(((x, [y]) for x, y in zip(relevant_points, prediction)))
    setpoints = set(relevant_points)

    for points, pred in all_preds:
        intersection = setpoints & set(tuple(tuple(x) for x in points.tolist()))
        for point in intersection:
            predcollation[point].append(pred[point == points])

    allpoints, allpreds = [], []
    for key, value in predcollation.items():
        if len(value) > 1:
            value = np.argmax(value) == 0
        else:
            value = value[0]
        allpoints.append(key)
        allpreds.append(value)

    return np.array(allpoints), np.array(allpreds)


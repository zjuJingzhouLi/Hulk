"""
Copyright (c) Microsoft Corporation.
Licensed under the MIT license.

"""
import cv2
import math
import json
from PIL import Image
import os
import os.path as op
import numpy as np
import code
import scipy.misc
from tqdm import tqdm
import yaml
import errno

import logging
import os
import os.path as op
import json
import numpy as np
import base64
import cv2
import yaml
import pickle

def generate_lineidx(filein, idxout):
    idxout_tmp = idxout + '.tmp'
    with open(filein, 'r') as tsvin, open(idxout_tmp,'w') as tsvout:
        fsize = os.fstat(tsvin.fileno()).st_size
        fpos = 0
        while fpos!=fsize:
            tsvout.write(str(fpos)+"\n")
            tsvin.readline()
            fpos = tsvin.tell()
    os.rename(idxout_tmp, idxout)


def read_to_character(fp, c):
    result = []
    while True:
        s = fp.read(32)
        assert s != ''
        if c in s:
            result.append(s[: s.index(c)])
            break
        else:
            result.append(s)
    return ''.join(result)


class TSVFile(object):
    def __init__(self, tsv_file, generate_lineidx=False):
        self.tsv_file = tsv_file
        self.lineidx = op.splitext(tsv_file)[0] + '.lineidx'
        self._fp = None
        self._lineidx = None
        # the process always keeps the process which opens the file.
        # If the pid is not equal to the currrent pid, we will re-open the file.
        self.pid = None
        # generate lineidx if not exist
        if not op.isfile(self.lineidx) and generate_lineidx:
            generate_lineidx(self.tsv_file, self.lineidx)

    def __del__(self):
        if self._fp:
            self._fp.close()

    def __str__(self):
        return "TSVFile(tsv_file='{}')".format(self.tsv_file)

    def __repr__(self):
        return str(self)

    def num_rows(self):
        self._ensure_lineidx_loaded()
        return len(self._lineidx)

    def seek(self, idx):
        self._ensure_tsv_opened()
        self._ensure_lineidx_loaded()
        try:
            pos = self._lineidx[idx]
        except:
            logging.info('{}-{}'.format(self.tsv_file, idx))
            raise
        self._fp.seek(pos)
        return [s.strip() for s in self._fp.readline().split('\t')]

    def seek_first_column(self, idx):
        self._ensure_tsv_opened()
        self._ensure_lineidx_loaded()
        pos = self._lineidx[idx]
        self._fp.seek(pos)
        return read_to_character(self._fp, '\t')

    def get_key(self, idx):
        return self.seek_first_column(idx)

    def __getitem__(self, index):
        return self.seek(index)

    def __len__(self):
        return self.num_rows()

    def _ensure_lineidx_loaded(self):
        if self._lineidx is None:
            logging.info('loading lineidx: {}'.format(self.lineidx))
            with open(self.lineidx, 'r') as fp:
                self._lineidx = [int(i.strip()) for i in fp.readlines()]

    def _ensure_tsv_opened(self):
        if self._fp is None:
            self._fp = open(self.tsv_file, 'r')
            self.pid = os.getpid()

        if self.pid != os.getpid():
            logging.info('re-open {} because the process id changed'.format(self.tsv_file))
            self._fp = open(self.tsv_file, 'r')
            self.pid = os.getpid()


class CompositeTSVFile():
    def __init__(self, file_list, seq_file):
        if isinstance(file_list, str):
            self.file_list = load_list_file(file_list)
        else:
            assert isinstance(file_list, list)
            self.file_list = file_list

        self.seq_file = seq_file
        self.initialized = False
        self.initialize()

    def get_key(self, index):
        idx_source, idx_row = self.seq[index]
        k = self.tsvs[idx_source].get_key(idx_row)
        return '_'.join([self.file_list[idx_source], k])

    def num_rows(self):
        return len(self.seq)

    def __getitem__(self, index):
        idx_source, idx_row = self.seq[index]
        return self.tsvs[idx_source].seek(idx_row)

    def __len__(self):
        return len(self.seq)

    def initialize(self):
        '''
        this function has to be called in init function if cache_policy is
        enabled. Thus, let's always call it in init funciton to make it simple.
        '''
        if self.initialized:
            return
        self.seq = []
        with open(self.seq_file, 'r') as fp:
            for line in fp:
                parts = line.strip().split('\t')
                self.seq.append([int(parts[0]), int(parts[1])])
        self.tsvs = [TSVFile(f) for f in self.file_list]
        self.initialized = True


def load_list_file(fname):
    with open(fname, 'r') as fp:
        lines = fp.readlines()
    result = [line.strip() for line in lines]
    if len(result) > 0 and result[-1] == '':
        result = result[:-1]
    return result

def load_linelist_file(linelist_file):
    if linelist_file is not None:
        line_list = []
        with open(linelist_file, 'r') as fp:
            for i in fp:
                line_list.append(int(i.strip()))
        return line_list

def find_file_path_in_yaml(fname, root='.'):
    if fname is not None:
        if op.isfile(fname):
            return fname
        elif op.isfile(op.join(root, fname)):
            return op.join(root, fname)
        else:
            raise FileNotFoundError(
                errno.ENOENT, os.strerror(errno.ENOENT), op.join(root, fname)
            )

def img_from_base64(imagestring):
    try:
        jpgbytestring = base64.b64decode(imagestring)
        nparr = np.frombuffer(jpgbytestring, np.uint8)
        r = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        return r
    except ValueError:
        return None

def MeshTSVDataset(img_file, label_file, hw_file, image_dir, pkl_file):  

    img_tsv = get_tsv_file(img_file)
    label_tsv = get_tsv_file(label_file)
    hw_tsv = get_tsv_file(hw_file)
    
    if hw_tsv:
        tsv = hw_tsv
        image_keys = [tsv.get_key(i) for i in range(tsv.num_rows())]
    elif label_tsv:
        tsv = label_tsv
        image_keys = [tsv.get_key(i) for i in range(tsv.num_rows())]
    dataset_len = img_tsv.num_rows()
    
    dataset_pkl = {}
    
    image_name_list = []
    annotations_list = []
    
    for idx in range(dataset_len):
        img_row = img_tsv[idx]
        # use -1 to support old format with multiple columns.
        cv2_im = img_from_base64(img_row[-1])
        cv2_im = cv2.cvtColor(cv2_im, cv2.COLOR_BGR2RGB)
        img_key = img_tsv[idx][0]
        
        hw_row = hw_tsv[idx]
        # json string format with "height" and "width" being the keys
        hw_info = json.loads(hw_row[1])[0]
        
    
        annotations_row = label_tsv[idx]
        annotations_key = annotations_row[0]
        
        assert annotations_key == img_key
        image_name_list.append(img_key)
        
        sub_dir = os.path.dirname(img_key)
        if not os.path.exists(image_dir+sub_dir):
            # import pdb;pdb.set_trace()
            os.makedirs(image_dir+sub_dir)
        cv2.imwrite(image_dir+img_key, cv2_im[:, :, ::-1])
        
        annotations = json.loads(annotations_row[1])
        annotations = annotations[0]
        annotations_list.append(annotations)
        
    dataset_pkl['image_name'] = image_name_list
    dataset_pkl['annotations'] = annotations_list
    
    with open(pkl_file, "wb") as f:
        pickle.dump(dataset_pkl, f)

def get_tsv_file(tsv_file):
    if tsv_file:
        tsv_path = find_file_path_in_yaml(tsv_file)
        return TSVFile(tsv_path)
if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Process five input arguments.")
    parser.add_argument("img_file", type=str, help="The .img.tsv file.")
    parser.add_argument("label_file", type=str, help="The .label.tsv file.")
    parser.add_argument("hw_file", type=str, help="The .hw.tsv file.")
    parser.add_argument("image_dir", type=str, help="The dict path to save transfered images.")
    parser.add_argument("pkl_file", type=str, help="The path to save the transfered annotation .pkl file")
    args = parser.parse_args()
    tsv_dataset = MeshTSVDataset(img_file=args.img_file, label_file=args.label_file, hw_file=args.hw_file, image_dir=args.image_dir, pkl_file=args.pkl_file)
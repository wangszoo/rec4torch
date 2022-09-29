from collections import namedtuple, OrderedDict
import torch
from torch import nn
from .layers import SequencePoolingLayer

DEFAULT_GROUP_NAME = "default_group"

class SparseFeat(namedtuple('SparseFeat', ['name', 'vocabulary_size', 'embedding_dim', 'dtype', 'embedding_name', 'group_name'])):
    """离散特征
    """
    def __new__(cls, name, vocabulary_size, embedding_dim=4, dtype="int32", embedding_name=None,
            group_name=DEFAULT_GROUP_NAME):
        if embedding_name is None:
            embedding_name = name
        if embedding_dim == "auto":
            embedding_dim = 6 * int(pow(vocabulary_size, 0.25))
        return super(SparseFeat, cls).__new__(cls, name, vocabulary_size, embedding_dim, dtype, embedding_name, group_name)


class VarLenSparseFeat(namedtuple('VarLenSparseFeat', ['sparsefeat', 'maxlen', 'pooling', 'length_name'])):
    """变长离散特征
    """
    def __new__(cls, sparsefeat, maxlen, pooling='mean', length_name=None):
            return super(VarLenSparseFeat, cls).__new__(cls, sparsefeat, maxlen, pooling, length_name)
    
    @property
    def name(self):
        return self.sparsefeat.name

    @property
    def vocabulary_size(self):
        return self.sparsefeat.vocabulary_size

    @property
    def embedding_dim(self):
        return self.sparsefeat.embedding_dim

    @property
    def use_hash(self):
        return self.sparsefeat.use_hash

    @property
    def dtype(self):
        return self.sparsefeat.dtype

    @property
    def embedding_name(self):
        return self.sparsefeat.embedding_name

    @property
    def group_name(self):
        return self.sparsefeat.group_name

class DenseFeat(namedtuple('DenseFeat', ['name', 'dimension', 'dtype'])):
    """连续特征
    """
    def __new__(cls, name, dimension=1, dtype="float32"):
        return super(DenseFeat, cls).__new__(cls, name, dimension, dtype)

    def __hash__(self):
        return self.name.__hash__()


def get_feature_names(feature_columns):
    """获取特征名称
    """
    features = build_input_features(feature_columns)
    return list(features.keys())


def build_input_features(feature_columns):
    """feat_name到col_range之间的映射
    """
    features = OrderedDict()

    start = 0
    for feat in feature_columns:
        feat_name = feat.name
        if feat_name in features:
            continue
        if isinstance(feat, SparseFeat):
            features[feat_name] = (start, start + 1)
            start += 1
        elif isinstance(feat, DenseFeat):
            features[feat_name] = (start, start + feat.dimension)
            start += feat.dimension
        elif isinstance(feat, VarLenSparseFeat):
            features[feat_name] = (start, start + feat.maxlen)
            start += feat.maxlen
            if feat.length_name is not None and feat.length_name not in features:
                features[feat.length_name] = (start, start + 1)
                start += 1
        else:
            raise TypeError("Invalid feature column type,got", type(feat))
    return features


def combined_dnn_input(sparse_embedding_list, dense_value_list):
    """合并sparse和dense
    """
    res = []
    if len(sparse_embedding_list) > 0:
        res.append(torch.flatten(torch.cat(sparse_embedding_list, dim=-1)))
    if len(dense_value_list) > 0:
        res.append(torch.flatten(torch.cat(dense_value_list, dim=-1)))

    if res:
        return torch.cat(res, dim=-1)
    else:
        raise NotImplementedError


def create_embedding_matrix(feature_columns, init_std=0.0001, linear=False, sparse=False, device='cpu'):
    """为Sparse, VarLenSparse进行embedding
       返回{embedding_name: nn.EmbeddingBag}
    """
    sparse_feature_columns = list(filter(lambda x: isinstance(x, SparseFeat), feature_columns)) if len(feature_columns) else []
    var_sparse_feature_columns = list(filter(lambda x: isinstance(x, VarLenSparseFeat), feature_columns)) if len(feature_columns) else []
    embedding_dict = nn.ModuleDict(
        {feat.embedding_name: nn.Embedding(feat.vocabulary_size, feat.embedding_dim if not linear else 1, sparse=sparse) 
        for feat in sparse_feature_columns+var_sparse_feature_columns}
    )
    return embedding_dict


def varlen_embedding_lookup(X, embedding_dict, feature_index, varlen_sparse_feature_columns):
    """变长离散特征经embedding并返回
    embedding_dict: 特征对应的embedding
    feature_index：特征对应的col区间
    """
    varlen_embedding_vec_dict = {}
    for fc in varlen_sparse_feature_columns:
        feature_name = fc.name
        embedding_name = fc.embedding_name
        if fc.use_hash:
            lookup_idx = feature_index[feature_name]
        else:
            lookup_idx = feature_index[feature_name]
        varlen_embedding_vec_dict[feature_name] = embedding_dict[embedding_name](
            X[:, lookup_idx[0]:lookup_idx[1]].long())  # (lookup_idx)

    return varlen_embedding_vec_dict


def get_varlen_pooling_list(embedding_dict, features, feature_index, varlen_sparse_feature_columns):
    """获取变长稀疏特征pooling的结果
    embedding_dict: {feat_name: input_embedding, ...}  [btz, seq_len, emb_size]
    features: [btz, seq_len]
    """
    varlen_sparse_embedding_list = []
    for feat in varlen_sparse_feature_columns:
        seq_emb = embedding_dict[feat.name]
        if feat.length_name is None:
            seq_mask = features[:, feature_index[feat.name][0]:feature_index[feat.name][1]].long()
            emb = SequencePoolingLayer(mode=feat.pooling, support_masking=True)([seq_emb, seq_mask])
        else:
            seq_length = features[:, feature_index[feat.length_name][0]:feature_index[feat.length_name][1]].long()
            emb = SequencePoolingLayer(mode=feat.combiner, supports_masking=False)([seq_emb, seq_length])
            
        varlen_sparse_embedding_list.append(emb)
    return varlen_sparse_embedding_list
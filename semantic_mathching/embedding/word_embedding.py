import enum
from abc import ABC, abstractmethod
from pickle import UnpicklingError

import gensim.models.keyedvectors as word2vec
import numpy as np
import tensorflow_hub as hub
from bert_serving.client import BertClient
from gensim.models import FastText
from gensim.models.fasttext import load_facebook_vectors
from nltk import word_tokenize, jaccard_distance, edit_distance
from sklearn.metrics.pairwise import cosine_similarity

from config import Config
from descriptor_processes.text_pre_process import space_cleaner
from embedding.word2vec_adaptor import sim_matrix_of_tokens, matrix_overall_sim

config = Config()
practical_zero = 0.000001


class EmbedTypes(enum.Enum):
    wm = 1
    w2v = 2
    nnlm = 3
    use = 4
    bert = 5
    random = 6
    fast = 7
    glove = 8
    jaccard = 9
    edit_distance = 10


class WordEmbedding(ABC):
    model = None
    sentence_level = False
    zeros = 0

    @abstractmethod
    def __init__(self, model_path):
        pass

    def calc_sim(self, a, b):
        score = 0
        if space_cleaner(a) and space_cleaner(a):
            score = self.calc_sim_by_model(a, b)
        if score < practical_zero and self.sentence_level:
            self.zeros += 1
        return score

    @abstractmethod
    def calc_sim_by_model(self, a, b):
        pass


class Wor2VecBase(WordEmbedding, ABC):
    def __init__(self, model_path):
        super().__init__(model_path)
        self.load_model(model_path)

    def load_model(self, model_path):
        try:
            self.model = word2vec.KeyedVectors.load_word2vec_format(model_path, binary=True)
        except UnicodeDecodeError:
            self.model = word2vec.KeyedVectors.load(model_path)


class WordMovers(Wor2VecBase):
    sentence_level = True

    def calc_sim_by_model(self, a, b):
        a_tokens = word_tokenize(a)
        b_tokens = word_tokenize(b)
        score = self.model.wmdistance(a_tokens, b_tokens)
        if score == np.inf:
            return 0
        return 1 / (1 + self.model.wmdistance(a_tokens, b_tokens))


class Word2VecS(Wor2VecBase):
    def calc_sim_by_model(self, a, b):
        matrix = sim_matrix_of_tokens(a, b, self._two_word_sim_decorator)
        score = matrix_overall_sim(matrix)
        return score

    def _two_word_sim_decorator(self, a, b):
        score = self._two_word_sim(a, b)
        if score < practical_zero:
            self.zeros += 1
        return score

    def _two_word_sim(self, a, b):
        try:
            similarity_score = self.model.similarity(a, b)
        except KeyError:
            similarity_score = 0
        return similarity_score


class Glove(Word2VecS):
    def __init__(self, model_path):
        super().__init__(model_path)

    def load_model(self, model_path):
        self.model = word2vec.KeyedVectors.load_word2vec_format(model_path)


class TensorFlowEmbed(WordEmbedding):
    sentence_level = True

    def __init__(self, model_path):
        super().__init__(model_path)
        self.model = hub.load(model_path)

    def calc_sim_by_model(self, a, b):
        x, y = self.model([a, b])
        xy = cosine_similarity([x], [y])[0][0]
        return xy


class RandomEmbed(WordEmbedding):

    def __init__(self, model_path):
        super().__init__(model_path)

    def calc_sim_by_model(self, a, b):
        # return np.random.random()
        return 0


class Bert(WordEmbedding):
    sentence_level = True

    # start a server first
    # bert-serving-start -model_dir /Users/usiusi/Documents/Report/WordEmbedding/models/uncased_L-12_H-768_A-12 -num_worker=1
    def __init__(self, model_path):
        self.bc = BertClient(check_length=False)
        super().__init__(model_path)

    def calc_sim_by_model(self, a, b):
        if a == '' or b == '':
            return 0
        vectors = self.bc.encode([a, b])
        return cosine_similarity([vectors[0]], [vectors[1]])[0][0]


class Fast(Word2VecS):

    def __init__(self, model_path):
        super().__init__(model_path)

    def load_model(self, model_path):
        try:
            self.model = FastText.load(model_path)
        except UnpicklingError:
            self.model = load_facebook_vectors(model_path)

    def _two_word_sim(self, a, b):
        try:
            similarity_score = self.model.wv.similarity(a, b)
        except KeyError:
            similarity_score = 0
        return similarity_score


class Jaccard(WordEmbedding):
    sentence_level = True

    def __init__(self, model_path):
        super().__init__(model_path)

    def calc_sim_by_model(self, a, b):
        a_tokens = word_tokenize(a)
        b_tokens = word_tokenize(b)
        if a_tokens.__len__() == 0 or b_tokens.__len__() == 0:
            return 0
        distance = jaccard_distance(set(a_tokens), set(b_tokens))
        return 1 - distance


class EditDistance(Word2VecS):

    def __init__(self, model_path):
        super().__init__(model_path)

    def load_model(self, model_path):
        pass

    def _two_word_sim(self, a, b):
        max_len = len(a) if len(a) > len(b) else len(b)
        return (max_len - edit_distance(a, b)) / max_len


model_cache = {}

embeddings = {
    EmbedTypes.wm: WordMovers,
    EmbedTypes.w2v: Word2VecS,
    EmbedTypes.glove: Glove,
    EmbedTypes.nnlm: TensorFlowEmbed,
    EmbedTypes.use: TensorFlowEmbed,
    EmbedTypes.bert: Bert,
    EmbedTypes.random: RandomEmbed,
    EmbedTypes.fast: Fast,
    EmbedTypes.jaccard: Jaccard,
    EmbedTypes.edit_distance: EditDistance
}


def embedding_factory(embedding_type: EmbedTypes, train_set) -> WordEmbedding:
    key = embedding_type.name + train_set
    if key in model_cache.keys():
        model_cache[key].zeros = 0
        return model_cache[key]
    else:
        model_cache.clear()
        model_path = get_model_path(embedding_type, train_set)
        model = embeddings[embedding_type](model_path)
        model_cache[key] = model
    return model


def get_model_path(embedding_type, train_set):
    no_model_techniques = [EmbedTypes.jaccard, EmbedTypes.edit_distance, EmbedTypes.random]
    if embedding_type in no_model_techniques:
        return ''
    name = embedding_type.name
    if name == 'wm':
        name = 'w2v'

    model_name = name + '_' + train_set
    return config.model_path[model_name]

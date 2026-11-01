"""Bounded content-keyed in-process index, rebuilt automatically on content change.

No pickle/disk deserialization. The fixed vocabulary/IDF/SVD are corpus-only;
queries never refit a model. This cache contains public source text, not chats.
"""
from functools import lru_cache
from collections import Counter
import math
import numpy as np


@lru_cache(maxsize=8)
def lexical_index(documents):
    counts = tuple(Counter(doc) for doc in documents)
    lengths = tuple(sum(c.values()) for c in counts)
    df = Counter(term for c in counts for term in c)
    return counts, lengths, df, sum(lengths)/max(1,len(lengths))


@lru_cache(maxsize=8)
def semantic_index(documents):
    vocabulary = sorted(set().union(*(set(d) for d in documents)))
    if len(vocabulary) > 4096 or len(documents) > 256:
        return None
    indices = {word:i for i,word in enumerate(vocabulary)}
    matrix = np.zeros((len(documents),len(vocabulary)))
    for row, words in enumerate(documents):
        for word in words: matrix[row,indices[word]] = 1
    idf = np.log((len(documents)+1)/(np.count_nonzero(matrix,axis=0)+1))+1
    matrix *= idf
    lexical = matrix / np.maximum(np.linalg.norm(matrix,axis=1,keepdims=True),1e-12)
    vt = None
    projected = None
    if len(documents) >= 3 and len(vocabulary):
        _,_,basis = np.linalg.svd(matrix,full_matrices=False)
        vt = basis[:min(8,len(documents)-1)]
        projected = matrix @ vt.T
        projected /= np.maximum(np.linalg.norm(projected,axis=1,keepdims=True),1e-12)
    return indices,idf,lexical,vt,projected


def semantic_scores(query_tokens, documents):
    index = semantic_index(documents)
    if index is None:
        df = Counter(w for d in documents for w in set(d))
        weights = {w:(math.log((len(documents)+1)/(v+1))+1)**2 for w,v in df.items()}
        query = set(query_tokens) & weights.keys()
        qnorm = math.sqrt(sum(weights[w] for w in query))
        return [sum(weights[w] for w in query & set(d))/(qnorm*math.sqrt(sum(weights[w] for w in set(d))) or 1) for d in documents]
    indices,idf,lexical,vt,projected = index
    q = np.zeros(len(indices))
    for word in query_tokens:
        if word in indices: q[indices[word]] = 1
    q *= idf
    scores = lexical @ (q/max(np.linalg.norm(q),1e-12))
    if vt is not None:
        latent = q @ vt.T
        latent /= max(np.linalg.norm(latent),1e-12)
        scores = .7*scores + .3*np.maximum(0,projected @ latent)
    return scores

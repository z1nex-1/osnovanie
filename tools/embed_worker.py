import json
import sys

from fastembed import TextEmbedding

model = TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
texts = json.load(sys.stdin)
vectors = [[round(float(x), 5) for x in v] for v in model.embed(texts)]
json.dump(vectors, sys.stdout)

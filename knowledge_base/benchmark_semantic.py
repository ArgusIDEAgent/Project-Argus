"""Reproducible real-embedding retrieval benchmark; never calls an answer LLM.

This is a small curated smoke benchmark, not a claim of general code accuracy.
Compare the same code units embedded raw vs with structural context. An optional
FastEmbed model name permits comparing supported candidate embedding models.
"""
import argparse
import json
from pathlib import Path
import sys
import time

import faiss
import numpy as np

from knowledge_base.chunking import chunk_source

FIXTURES = [
    ('auth.ts','typescript','''/** Check whether an actor has permission to access a protected resource. */
export function authorizeRequest(actor: { permissions: string[] }, permission: string) {
  if (!actor.permissions.includes(permission)) throw new Error("ACCESS_DENIED");
  return true;
}
/** Reject expired authentication sessions. */
export function verifySession(expiresAt: number) { return Date.now() < expiresAt; }
'''),
    ('events.js','javascript','''/** Ignore duplicate events by remembering previously processed identifiers. */
export function deduplicateEvent(id, seen) { if (seen.has(id)) return false; seen.add(id); return true; }
/** Send email notifications to registered subscribers. */
export function notifySubscribers(subscribers, message) { return subscribers.map(email => sendMail(email, message)); }
'''),
    ('cache.py','python','''def memoize_result(key, value, cache):
    """Cache computed values to avoid doing expensive calculations again."""
    cache[key] = value
    return value

def evict_old_entries(cache, now):
    """Remove cached entries whose expiration time has passed."""
    return {key: item for key, item in cache.items() if item.expiry > now}
'''),
    ('payments.py','python','''def settle_invoice(invoice, gateway):
    """Charge a customer's payment card for an unpaid invoice."""
    return gateway.charge(invoice.card, invoice.total)

def refund_purchase(payment, gateway):
    """Return money to the customer after an order is cancelled."""
    return gateway.refund(payment.identifier)
'''),
    ('charts.ts','typescript','''/** Render a bar chart from numeric values. */
export function drawChart(values: number[]) { return values.map(value => ({height: value})); }
/** Sort displayed products by their selling price. */
export function sortProducts(products: {price: number}[]) { return products.sort((a,b) => a.price-b.price); }
'''),
    ('uploads.ts','typescript','''/** Store an uploaded image under a random filename on disk. */
export function persistUpload(bytes: Uint8Array, folder: string) { return writeFile(folder + "/" + randomUUID(), bytes); }
/** Resize a photo before storing a small preview thumbnail. */
export function createThumbnail(image: Image) { return image.resize(128,128); }
'''),
]
QUESTIONS = [
    ('Where is the logic that checks whether a user is allowed to access a resource?','auth.ts','authorizeRequest'),
    ('Where are expired login sessions rejected?','auth.ts','verifySession'),
    ('How are repeated events prevented from being processed twice?','events.js','deduplicateEvent'),
    ('Where are notification emails sent to subscribers?','events.js','notifySubscribers'),
    ('Where are expensive computed results saved for reuse?','cache.py','memoize_result'),
    ('Where are expired cache items removed?','cache.py','evict_old_entries'),
    ('Where is a customer charged for an invoice?','payments.py','settle_invoice'),
    ('How is money returned after cancelling a purchase?','payments.py','refund_purchase'),
    ('Where are numbers drawn as bars?','charts.ts','drawChart'),
    ('How are products ordered by cost?','charts.ts','sortProducts'),
    ('Where is an uploaded image written to disk?','uploads.ts','persistUpload'),
    ('Where is a small image preview created?','uploads.ts','createThumbnail'),
]


def run(model=None):
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ai-engine'))
    import config
    from fastembed import TextEmbedding
    started=time.monotonic()
    embedder = config.get_embedding_model() if not model or model==config.EMBEDDING_MODEL_NAME else TextEmbedding(
        model_name=model,cache_dir=str(Path(config.DEFAULT_DATA_DIR)/'models'))
    units=[]
    for path,language,source in FIXTURES:
        units.extend((path,c) for c in chunk_source(source,path=path,repo_id='0'*24,language=language))
    queries=np.asarray(list(embedder.embed([q[0] for q in QUESTIONS])),dtype='float32')
    faiss.normalize_L2(queries)
    report={'model':model or config.EMBEDDING_MODEL_NAME,'questions':len(QUESTIONS),'chunks':len(units),'languages':sorted({f[1] for f in FIXTURES})}
    for variant in ('raw_units','structural_context'):
        texts=[chunk.text if variant=='raw_units' else chunk.embedding_text(path) for path,chunk in units]
        vectors=np.asarray(list(embedder.embed(texts)),dtype='float32')
        faiss.normalize_L2(vectors)
        index=faiss.IndexFlatIP(vectors.shape[1]); index.add(vectors)
        scores,ids=index.search(queries,3)
        details=[]
        for expected,ranked,similarities in zip(QUESTIONS,ids,scores):
            matches=[(units[i][0],units[i][1].name)==expected[1:] for i in ranked]
            details.append({'query':expected[0],'expected':f'{expected[1]}::{expected[2]}',
                'rank':next((i+1 for i,hit in enumerate(matches) if hit),None),
                'top1':f'{units[ranked[0]][0]}::{units[ranked[0]][1].name}',
                'top1_score':round(float(similarities[0]),4)})
        report[variant]={'recall_at_1':sum(d['rank']==1 for d in details)/len(details),
            'recall_at_3':sum(d['rank'] is not None for d in details)/len(details),'details':details}
    report['elapsed_seconds']=round(time.monotonic()-started,2)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model',help='Optional supported FastEmbed model to evaluate')
    args=parser.parse_args()
    print(json.dumps(run(args.model),indent=2))


if __name__=='__main__':
    main()

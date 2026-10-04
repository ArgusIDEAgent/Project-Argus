'use strict';

const { searchCode } = require('./retrievalService');
const { status } = require('./semanticStore');
const { one } = require('./graphStore');

function calibrateScores(samples) {
  if (!samples.some(item => item.label !== 'DISTINCT') || !samples.some(item => item.label === 'DISTINCT')) {
    throw new Error('Calibration requires positive and negative labels.');
  }
  let best;
  for (let step = 50; step <= 99; step++) {
    const extend = step / 100;
    let tp = 0, fp = 0, fn = 0;
    for (const sample of samples) {
      const positive = sample.label !== 'DISTINCT';
      const predicted = sample.score >= extend;
      if (predicted && positive) tp++;
      if (predicted && !positive) fp++;
      if (!predicted && positive) fn++;
    }
    const precision = tp / (tp + fp || 1);
    const recall = tp / (tp + fn || 1);
    if (precision < 0.9) continue;
    if (!best || recall > best.recall || (recall === best.recall && precision > best.precision)) {
      best = { extend, reuse: Math.min(0.99, Math.max(0.88, extend + 0.1)), precision, recall, sampleCount: samples.length };
    }
  }
  if (!best || !best.recall) throw new Error('Labels do not support a useful threshold at 90% precision. Add better examples.');
  return best;
}

async function calibrate(registry, provider, body) {
  if (!body || !/^[a-f0-9]{24}$/.test(body.repoId) || !Array.isArray(body.examples) ||
      body.examples.length < 4 || body.examples.length > 30 || body.examples.some(example =>
        !example || typeof example.query !== 'string' || !example.query.trim() || example.query.length > 2000 ||
        !/^[a-f0-9]{40}$/.test(example.entityId) || !['REUSE', 'EXTEND', 'DISTINCT'].includes(example.label))) {
    throw new Error('Provide 4 to 30 labeled query/entity pairs for calibration.');
  }
  const before = await registry.withDb(db => status(db, body.repoId));
  const seen = new Set();
  await registry.withDb(db => {
    for (const example of body.examples) {
      const key = `${example.query}\0${example.entityId}`;
      if (seen.has(key)) throw new Error('Duplicate calibration pair.');
      seen.add(key);
      if (!one(db, 'SELECT entity_id FROM semantic_chunks WHERE repo_id = ? AND entity_id = ?', [body.repoId, example.entityId])) {
        throw new Error('Calibration entity is not in this repository.');
      }
    }
  });
  const samples = [];
  for (const example of body.examples) {
    const result = await searchCode(registry, provider, { repoId: body.repoId, query: example.query, limit: 20 });
    if (result.mode !== 'hybrid') throw new Error('Calibration requires an available embedding model.');
    const candidate = result.results.find(item => item.entityId === example.entityId);
    samples.push({ label: example.label, score: candidate?.scores.semantic || 0 });
  }
  const metrics = calibrateScores(samples);
  const thresholds = { reuse: metrics.reuse, extend: metrics.extend };
  return registry.withDb(db => {
    const current = status(db, body.repoId);
    if (!current || current.refreshedAt !== before?.refreshedAt) throw new Error('Index changed during calibration. Retry.');
    db.run('INSERT OR REPLACE INTO semantic_calibration VALUES (?, ?, ?, ?, ?)',
      [body.repoId, current.model, JSON.stringify(thresholds), JSON.stringify(metrics), new Date().toISOString()]);
    return { thresholds, metrics, model: current.model, note: 'Calibration-set metrics; validate separately on held-out examples.' };
  }, true);
}

module.exports = { calibrate, calibrateScores };

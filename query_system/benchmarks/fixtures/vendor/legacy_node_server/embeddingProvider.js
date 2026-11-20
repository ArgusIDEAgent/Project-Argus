'use strict';

function normalize(vector) {
  if (!Array.isArray(vector) || !vector.length || vector.length > 16384 ||
      vector.some(value => typeof value !== 'number' || !Number.isFinite(value))) throw new Error('Invalid embedding vector.');
  const norm = Math.hypot(...vector);
  if (!norm || !Number.isFinite(norm)) throw new Error('Empty embedding vector.');
  return vector.map(value => value / norm);
}

class EmbeddingProvider {
  constructor({ url = process.env.CODEMIND_EMBEDDING_URL || 'http://127.0.0.1:11434',
    model = process.env.CODEMIND_EMBEDDING_MODEL || 'nomic-embed-text' } = {}) {
    const endpoint = new URL(url);
    if (endpoint.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(endpoint.hostname) ||
        endpoint.username || endpoint.password || endpoint.pathname !== '/' || endpoint.search || endpoint.hash) {
      throw new Error('Embeddings require a local loopback Ollama URL.');
    }
    if (!model || /cloud/i.test(model)) throw new Error('A local embedding model is required.');
    this.url = endpoint.origin;
    this.model = model;
  }

  async request(route, body, timeout = 60000) {
    const response = await fetch(this.url + route, { method: body ? 'POST' : 'GET',
      redirect: 'error', signal: AbortSignal.timeout(timeout),
      headers: { 'content-type': 'application/json' }, ...(body ? { body: JSON.stringify(body) } : {}) });
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(`Local embedding service returned ${response.status}: ${String(detail.error || 'request failed').slice(0, 250)}`);
    }
    return response.json();
  }

  async identity() {
    const data = await this.request('/api/tags', undefined, 3000);
    const canonical = this.model.includes(':') ? this.model : `${this.model}:latest`;
    const model = data.models?.find(item => item.name === canonical || item.name === this.model);
    if (!model?.digest || model.remote_host || model.remote_model) {
      throw new Error(`Local embedding model missing. Run: ollama pull ${this.model}`);
    }
    return `${this.model}@${model.digest}:input-v1`;
  }

  async embed(texts, kind = 'document') {
    if (!texts.length) return [];
    const prefix = this.model.startsWith('nomic-embed-text') ? (kind === 'query' ? 'search_query: ' : 'search_document: ') : '';
    const data = await this.request('/api/embed', { model: this.model, input: texts.map(text => prefix + text),
      truncate: false, options: { num_ctx: 8192 } });
    if (!Array.isArray(data.embeddings) || data.embeddings.length !== texts.length) throw new Error('Embedding batch length mismatch.');
    const vectors = data.embeddings.map(normalize);
    if (vectors.some(vector => vector.length !== vectors[0].length)) throw new Error('Embedding dimension mismatch.');
    return vectors;
  }
}

module.exports = { EmbeddingProvider, normalize };

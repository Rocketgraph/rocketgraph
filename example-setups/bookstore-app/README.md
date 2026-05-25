# Bookstore App

A simple Express bookstore API with in-memory data. No database, no Redis — just a clean Node.js app to demo Rocketgraph auto-instrumentation.

## Setup

```bash
npm install
npm run dev
```

## Auto-instrument with Rocketgraph

```bash
npx @rgraph/otel-node init
```

This will:
1. Detect Express + TypeScript
2. Install OpenTelemetry packages
3. Generate `src/instrumentation.ts`
4. Add the import to your entry point

Then set your env vars and run:

```bash
export ROCKETGRAPH_API_KEY=your_key
export OTEL_SERVICE_NAME=bookstore-app
export OTEL_EXPORTER_OTLP_ENDPOINT=https://ingress.us-east-2.rocketgraph.app
npm run dev
```

## Endpoints

```
GET  /health
GET  /api/books              — list all books (optional ?genre=sci-fi)
GET  /api/books/:id          — get a book
POST /api/cart/:user/add     — add to cart { bookId, qty }
GET  /api/cart/:user         — view cart
POST /api/checkout/:user     — place order
GET  /api/orders/:user       — list orders
```

## Test it

```bash
# Browse books
curl localhost:3001/api/books

# Add to cart
curl -X POST localhost:3001/api/cart/luke/add -H 'Content-Type: application/json' -d '{"bookId":"b1","qty":2}'

# Checkout
curl -X POST localhost:3001/api/checkout/luke
```

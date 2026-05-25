import express from 'express'
import cors from 'cors'

const app = express()
app.use(cors())
app.use(express.json())

// ── In-memory data ───────────────────────────────────────────────────────────

interface Book {
  id: string
  title: string
  author: string
  price: number
  genre: string
  stock: number
}

const books: Book[] = [
  { id: 'b1', title: 'Dune', author: 'Frank Herbert', price: 14.99, genre: 'sci-fi', stock: 12 },
  { id: 'b2', title: 'Neuromancer', author: 'William Gibson', price: 12.99, genre: 'sci-fi', stock: 8 },
  { id: 'b3', title: 'The Hobbit', author: 'J.R.R. Tolkien', price: 11.99, genre: 'fantasy', stock: 15 },
  { id: 'b4', title: 'Foundation', author: 'Isaac Asimov', price: 13.99, genre: 'sci-fi', stock: 6 },
  { id: 'b5', title: 'Snow Crash', author: 'Neal Stephenson', price: 15.99, genre: 'sci-fi', stock: 4 },
  { id: 'b6', title: '1984', author: 'George Orwell', price: 9.99, genre: 'dystopian', stock: 20 },
  { id: 'b7', title: 'Brave New World', author: 'Aldous Huxley', price: 10.99, genre: 'dystopian', stock: 10 },
  { id: 'b8', title: 'The Left Hand of Darkness', author: 'Ursula K. Le Guin', price: 13.49, genre: 'sci-fi', stock: 7 },
]

interface CartItem { bookId: string; qty: number }
const carts: Record<string, CartItem[]> = {}
const orders: { id: string; user: string; items: CartItem[]; total: number; createdAt: string }[] = []

let orderCounter = 0

// ── Routes ───────────────────────────────────────────────────────────────────

// Health
app.get('/health', (_req, res) => {
  res.json({ status: 'ok' })
})

// List books
app.get('/api/books', (req, res) => {
  const genre = req.query.genre as string | undefined
  const result = genre ? books.filter(b => b.genre === genre) : books
  console.log(`[books] listing ${result.length} books${genre ? ` (genre=${genre})` : ''}`)
  res.json(result)
})

// Get book
app.get('/api/books/:id', (req, res) => {
  const book = books.find(b => b.id === req.params.id)
  if (!book) {
    console.error(`[books] book ${req.params.id} not found`)
    res.status(404).json({ error: 'Book not found' })
    return
  }
  res.json(book)
})

// Add to cart
app.post('/api/cart/:user/add', (req, res) => {
  const { user } = req.params
  const { bookId, qty = 1 } = req.body

  const book = books.find(b => b.id === bookId)
  if (!book) {
    console.error(`[cart] book ${bookId} not found for user ${user}`)
    res.status(404).json({ error: 'Book not found' })
    return
  }
  if (book.stock < qty) {
    console.warn(`[cart] insufficient stock for ${book.title}: ${book.stock} available, ${qty} requested`)
    res.status(400).json({ error: 'Insufficient stock' })
    return
  }

  if (!carts[user]) carts[user] = []
  const existing = carts[user].find(i => i.bookId === bookId)
  if (existing) {
    existing.qty += qty
  } else {
    carts[user].push({ bookId, qty })
  }

  console.log(`[cart] ${user} added ${qty}x ${book.title}`)
  res.json({ cart: carts[user] })
})

// View cart
app.get('/api/cart/:user', (req, res) => {
  const cart = carts[req.params.user] || []
  const items = cart.map(i => {
    const book = books.find(b => b.id === i.bookId)
    return { ...i, book }
  })
  res.json({ items })
})

// Checkout
app.post('/api/checkout/:user', (req, res) => {
  const { user } = req.params
  const cart = carts[user]

  if (!cart || cart.length === 0) {
    console.warn(`[checkout] ${user} tried to checkout with empty cart`)
    res.status(400).json({ error: 'Cart is empty' })
    return
  }

  // Calculate total and deduct stock
  let total = 0
  for (const item of cart) {
    const book = books.find(b => b.id === item.bookId)
    if (!book) {
      console.error(`[checkout] book ${item.bookId} disappeared during checkout for ${user}`)
      res.status(500).json({ error: 'Internal error — book not found' })
      return
    }
    if (book.stock < item.qty) {
      console.error(`[checkout] stock race condition: ${book.title} has ${book.stock} but ${user} needs ${item.qty}`)
      res.status(409).json({ error: `${book.title} is out of stock` })
      return
    }
    book.stock -= item.qty
    total += book.price * item.qty
  }

  const order = {
    id: `ord-${++orderCounter}`,
    user,
    items: [...cart],
    total: Math.round(total * 100) / 100,
    createdAt: new Date().toISOString(),
  }
  orders.push(order)
  delete carts[user]

  console.log(`[checkout] ${user} placed order ${order.id} — $${order.total}`)
  res.json(order)
})

// List orders
app.get('/api/orders/:user', (req, res) => {
  const userOrders = orders.filter(o => o.user === req.params.user)
  res.json(userOrders)
})

// ── Start ────────────────────────────────────────────────────────────────────

const PORT = parseInt(process.env.PORT || '3001', 10)
app.listen(PORT, () => {
  console.log(`Bookstore API listening on http://localhost:${PORT}`)
})

from generation.context_check import check_context_usage

STORE = '''
import { createContext, useContext, useState, useMemo } from "react";
const ProductsContext = createContext(null);
const CartContext = createContext(null);
const ContactContext = createContext(null);

export function ProductsProvider({ children }) {
  const [products, setProducts] = useState([]);
  const [loading, setLoading] = useState(false);
  const load = async () => { try { setLoading(true); } catch (e) { console.log({ e }); } };
  const value = { products, loading, loadProducts: load, nested: { a: 1 } };
  return <ProductsContext.Provider value={value}>{children}</ProductsContext.Provider>;
}
export function CartProvider({ children }) {
  const [items, setItems] = useState([]);
  const value = useMemo(() => ({
    items,
    addToCart: (p) => { setItems((prev) => [...prev, p]); },
    clearCart() { setItems([]); },
  }), [items]);
  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}
export function ContactProvider({ children }) {
  return <ContactContext.Provider value={{ sent: false, send: () => {} }}>{children}</ContactContext.Provider>;
}
export function useProducts() { return useContext(ProductsContext); }
export const useCart = () => useContext(CartContext);
export function useContact() { return useContext(ContactContext); }
'''


def _texts(**pages):
    texts = {"src/context/StoreContext.jsx": STORE}
    texts.update({f"src/pages/{k}.jsx": v for k, v in pages.items()})
    return texts


def test_catches_missing_property_in_multi_context_file():
    problems = check_context_usage(_texts(
        Home="const { products, fetchProducts } = useProducts();"))
    issues = problems["src/pages/Home.jsx"]
    assert len(issues) == 1 and "fetchProducts" in issues[0]
    assert "loadProducts" in issues[0]  # tells the model what exists


def test_valid_usage_passes_including_aliases_and_nested():
    problems = check_context_usage(_texts(
        Home="const { products, loading: isLoading, loadProducts, nested } = useProducts();",
        Cart="const { items, addToCart: add, clearCart } = useCart();",
        Contact="const { sent, send } = useContact();"))
    assert problems == {}


def test_each_hook_checked_against_its_own_context():
    problems = check_context_usage(_texts(
        Cart="const { items, totalAmount } = useCart();"))
    assert "totalAmount" in problems["src/pages/Cart.jsx"][0]
    assert "CartContext" in problems["src/pages/Cart.jsx"][0]


def test_spread_in_value_is_skipped_not_guessed():
    store = ('const C = createContext(null);\n'
             'export function P({children}){ const value = { ...api, a };\n'
             'return <C.Provider value={value}>{children}</C.Provider>; }\n'
             'export function useC(){ return useContext(C); }\n')
    texts = {"src/context/X.jsx": store, "src/pages/H.jsx": "const { zzz } = useC();"}
    assert check_context_usage(texts) == {}


def test_no_contexts_no_problems():
    assert check_context_usage({"src/pages/H.jsx": "const { a } = useThing();"}) == {}

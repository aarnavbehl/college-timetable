// Hash-router helpers (separate from main.js so pages can navigate without circular imports).
let rerenderFn = () => {};

export function setRerender(fn) { rerenderFn = fn; }
export function rerender() { rerenderFn(); }

export function navigate(path) {
  if (location.hash === `#${path}`) rerender(); else location.hash = `#${path}`;
}

export function currentPath() {
  return (location.hash.replace(/^#/, '') || '/').split('?')[0];
}

/** Query parameters of the current hash, e.g. #/simulations?id=3 -> URLSearchParams {id: '3'} */
export function currentQuery() {
  return new URLSearchParams((location.hash.split('?')[1]) || '');
}

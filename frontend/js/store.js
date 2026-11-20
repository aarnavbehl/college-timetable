// Tiny shared state: who is signed in and the college's week settings.
import { api } from './api.js';

export const store = { user: null, settings: null };

export async function loadSettings() {
  store.settings = await api.get('/settings');
  return store.settings;
}

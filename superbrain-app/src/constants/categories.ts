/** Chip shown for the unfiltered feed. */
export const ALL_CATEGORY = { id: 'all', name: 'All', icon: 'star', count: 0 };

/**
 * Built-in legacy taxonomy (product/places/food/…).
 * Only used when the server reports `use_default_categories: true`.
 * When that flag is false, clients must not seed or show these chips.
 */
export const BUILTIN_DEFAULT_CATEGORIES = [
  { id: 'product', name: 'Product', icon: 'cube', count: 0 },
  { id: 'places', name: 'Places', icon: 'location', count: 0 },
  { id: 'food', name: 'Food', icon: 'restaurant', count: 0 },
  { id: 'software', name: 'Software', icon: 'code-slash', count: 0 },
  { id: 'book', name: 'Book', icon: 'book', count: 0 },
  { id: 'fitness', name: 'Fitness', icon: 'fitness', count: 0 },
  { id: 'film', name: 'Film', icon: 'film', count: 0 },
  { id: 'tv shows', name: 'TV Shows', icon: 'tv', count: 0 },
  { id: 'event', name: 'Event', icon: 'calendar', count: 0 },
  { id: 'other', name: 'Other', icon: 'pricetag', count: 0 },
];

/**
 * Offline / pre-taxonomy placeholder: All only.
 * Real chips come from `/taxonomy` after load.
 */
export const DEFAULT_CATEGORIES = [ALL_CATEGORY];

export const CATEGORY_ICONS: Record<string, string> = {
  'all': 'star',
  'sysadmin': 'terminal-outline',
  'science': 'flask-outline',
  'technology': 'hardware-chip-outline',
  'history': 'hourglass-outline',
  'humanities': 'library-outline',
  'politics': 'newspaper-outline',
  'product': 'cube-outline',
  'places': 'location-outline',
  'food': 'restaurant-outline',
  'recipe': 'restaurant-outline', // Legacy support
  'software': 'code-slash-outline',
  'book': 'book-outline',
  'fitness': 'fitness-outline',
  'workout': 'fitness-outline', // Legacy support
  'film': 'film-outline',
  'tv shows': 'tv-outline',
  'event': 'calendar-outline',
  'other': 'pricetag-outline',
  'uncategorized': 'help-circle-outline',
};

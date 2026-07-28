export const DEFAULT_CATEGORIES = [
  { id: 'all', name: 'All', icon: 'star', count: 0 },
  // Operator taxonomy (config-driven on server)
  { id: 'sysadmin', name: 'Sysadmin', icon: 'terminal', count: 0 },
  { id: 'science', name: 'Science', icon: 'flask', count: 0 },
  { id: 'technology', name: 'Technology', icon: 'hardware-chip', count: 0 },
  { id: 'history', name: 'History', icon: 'hourglass', count: 0 },
  { id: 'humanities', name: 'Humanities', icon: 'library', count: 0 },
  { id: 'politics', name: 'Politics', icon: 'newspaper', count: 0 },
  // Legacy built-in names (kept until migrated / for older posts)
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

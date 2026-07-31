/**
 * Sync service — orchestrates data flow between the backend API and localDb.
 *
 * Strategy:
 *   1. On first launch (empty local DB) → fullSync() fetches ALL posts in batches
 *   2. On subsequent opens → deltaSync() fetches only changed posts since last sync
 *   3. UI always reads from localDb — sync runs non-blocking in the background
 */

import localDb from './localDb';
import apiService from './api';
import { Post } from '../types';

const BATCH_SIZE = 200; // posts per batch during full sync

// ─── Full sync ─────────────────────────────────────────────────────

/**
 * Fetch ALL posts from backend in batches and store in local DB.
 * Called on first ever launch or after a database reset.
 * Returns total number of posts synced.
 */
async function fullSync(): Promise<number> {
  let offset = 0;
  let totalSynced = 0;
  let serverTotal = Infinity;

  while (offset < serverTotal) {
    const result = await apiService.getRecentPostsPaginated(BATCH_SIZE, offset);
    if (!result || result.data.length === 0) break;

    serverTotal = result.total;
    await localDb.upsertPosts(result.data);
    totalSynced += result.data.length;
    offset += result.data.length;
  }

  // Record sync time
  await localDb.setLastSyncTime(new Date().toISOString());

  console.log(`[Sync] Full sync complete: ${totalSynced} posts`);
  return totalSynced;
}

// ─── Delta sync ────────────────────────────────────────────────────

/**
 * Fetch only posts that changed since last sync.
 * Also fetches deleted shortcodes and removes them locally.
 * Returns number of posts updated.
 */
async function deltaSync(): Promise<number> {
  const since = await localDb.getLastSyncTime();
  if (!since) {
    // No sync time → fall back to full sync
    return fullSync();
  }

  // Fetch every changed row before advancing the cursor. A single delta can
  // exceed the server's page size after a large playlist import.
  const changedPosts: Post[] = [];
  let offset = 0;
  while (true) {
    const page = await apiService.syncPosts(since, BATCH_SIZE, offset);
    changedPosts.push(...page.data);
    offset += page.data.length;
    if (!page.hasMore || page.data.length === 0) break;
  }

  // Filter out hidden (soft-deleted) posts for upsert; delete them locally instead
  const toUpsert: Post[] = [];
  const toDeleteFromSync: string[] = [];
  for (const p of changedPosts) {
    if ((p as any).is_hidden === 1) {
      toDeleteFromSync.push(p.shortcode);
    } else {
      toUpsert.push(p);
    }
  }

  if (toUpsert.length > 0) {
    await localDb.upsertPosts(toUpsert);
  }

  // Fetch explicitly deleted posts
  const deletedItems = await apiService.getSyncDeleted(since);
  const deletedShortcodes = [
    ...toDeleteFromSync,
    ...deletedItems.map(d => d.shortcode),
  ];
  if (deletedShortcodes.length > 0) {
    await localDb.deletePosts(deletedShortcodes);
  }

  // Update sync cursor
  await localDb.setLastSyncTime(new Date().toISOString());

  const totalChanges = toUpsert.length + deletedShortcodes.length;
  if (totalChanges > 0) {
    console.log(`[Sync] Delta sync: ${toUpsert.length} upserted, ${deletedShortcodes.length} deleted`);
  }
  return totalChanges;
}

// ─── Smart sync entry point ────────────────────────────────────────

/**
 * Decides whether to do a full or delta sync.
 * - Empty local DB → full sync
 * - forceFull → full sync (pull-to-refresh after server-side migrations)
 * - Server taxonomy_version differs from last successful sync → full sync
 *   (category migrations bump version; delta alone can miss them if the
 *   device's last_synced_at cursor already advanced)
 * - Has data → delta sync
 * Returns true if any data changed.
 */
async function syncIfNeeded(forceFull: boolean = false): Promise<boolean> {
  try {
    const empty = await localDb.isEmpty();
    let taxonomyChanged = false;
    let taxonomyVersion = '';
    try {
      const taxonomy = await apiService.getTaxonomy();
      taxonomyVersion = taxonomy?.taxonomy_version || '';
      if (taxonomyVersion) {
        const localVersion = await localDb.getSyncMeta('taxonomy_version');
        taxonomyChanged = localVersion !== taxonomyVersion;
        if (taxonomyChanged) {
          console.log(
            `[Sync] Taxonomy version changed (${localVersion || 'none'} → ${taxonomyVersion}); forcing full sync`
          );
        }
      }
    } catch {
      /* offline / taxonomy endpoint unavailable — fall through */
    }

    if (empty || forceFull || taxonomyChanged) {
      const count = await fullSync();
      if (count > 0 && taxonomyVersion) {
        await localDb.setSyncMeta('taxonomy_version', taxonomyVersion);
      }
      return count > 0;
    } else {
      const changes = await deltaSync();
      return changes > 0;
    }
  } catch (error) {
    console.warn('[Sync] Sync failed (will retry later):', error);
    return false;
  }
}

// ─── Export ─────────────────────────────────────────────────────────

const syncService = {
  fullSync,
  deltaSync,
  syncIfNeeded,
};

export default syncService;

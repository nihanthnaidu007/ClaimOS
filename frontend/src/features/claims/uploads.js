// Attachment upload helper: sequential uploads with per-file results.
//
// A hook-per-file doesn't compose (hooks can't loop), so this is a plain
// helper over the authenticated api client; callers surface the per-file
// outcomes. The backend rejects duplicates by content hash, so retries are
// safe.
import api from '@/lib/api';

export async function uploadClaimFiles(claimId, files) {
  const results = [];
  for (const file of files) {
    try {
      const body = new FormData();
      body.append('file', file);
      await api.post(`/claims/${claimId}/documents`, body, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      results.push({ filename: file.name, status: 'uploaded' });
    } catch (err) {
      results.push({
        filename: file.name,
        status: 'rejected',
        error: err?.response?.data?.detail || 'Upload failed',
      });
    }
  }
  return results;
}

import { apiFetch } from './apiClient';

export interface UploadAvatarResponse {
  url: string;
  public_id?: string;
  provider?: string;
}

/**
 * Uploads user profile image to backend /api/upload/avatar.
 * Uses FormData so that the image is sent as multipart/form-data.
 */
export async function uploadAvatar(file: File): Promise<{ url: string | null; error?: string }> {
  const formData = new FormData();
  formData.append('file', file);

  const res = await apiFetch<UploadAvatarResponse>('/api/upload/avatar', {
    method: 'POST',
    body: formData,
  });

  if (!res.ok || !res.data?.url) {
    return { url: null, error: res.error || 'Failed to upload profile picture' };
  }

  return { url: res.data.url };
}

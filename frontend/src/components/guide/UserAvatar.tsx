/*
 * UserAvatar: the traveler's picture, falling back to a guide portrait (stable per user) instead of an initial.
 * Uploaded avatars win; everyone else gets a character, so no chat, menu or profile is ever a grey letter.
 */
import { GuideCharacter } from './GuideCharacter';
import { guideForSeed } from './guides';

interface Props {
  avatarUrl?: string | null;
  seed?: string | null;
  size: number;
  name?: string;
  className?: string;
}

export function UserAvatar({ avatarUrl, seed, size, name = '', className = '' }: Props) {
  if (avatarUrl) {
    return <img src={avatarUrl} alt={name} width={size} height={size} className={className}
      style={{ width: size, height: size, borderRadius: '50%', objectFit: 'cover' }} />;
  }
  return <GuideCharacter guide={guideForSeed(seed || 'guest')} size={size} still label={name || undefined} className={className} />;
}

import React from 'react';

import { GuideCharacter } from '../guide/GuideCharacter';
import { useGuide } from '../guide/GuideContext';

export type AssistantAvatarSize = 'sm' | 'header' | 'message' | 'welcome' | 'thinking';

interface AssistantAvatarProps {
  size?: AssistantAvatarSize | number;
  className?: string;
  alt?: string;
  isThinking?: boolean;
}

const SIZE_MAP: Record<AssistantAvatarSize, number> = {
  sm: 28,
  header: 34,
  message: 42,
  thinking: 72,
  welcome: 104,
};

/** The assistant is always the trip's guide (GuideContext), still unless it's thinking. */
export const AssistantAvatar: React.FC<AssistantAvatarProps> = ({ size = 'message', className = '', alt, isThinking = false }) => {
  const guide = useGuide();
  const pixelSize = typeof size === 'number' ? size : SIZE_MAP[size] || 42;
  return (
    <GuideCharacter guide={guide} size={pixelSize} mood={isThinking ? 'thinking' : 'idle'} still
      label={alt ?? (isThinking ? `${guide.name} is thinking` : guide.name)} className={className} />
  );
};

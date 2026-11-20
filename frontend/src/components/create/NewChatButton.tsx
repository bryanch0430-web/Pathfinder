import React from 'react';
import { PlusIcon } from '../home/v2/IconsV2';

interface NewChatButtonProps {
  onClick: () => void;
  className?: string;
  disabled?: boolean;
}

export const NewChatButton: React.FC<NewChatButtonProps> = ({ onClick, className = '', disabled = false }) => (
  <button
    type="button"
    onClick={onClick}
    disabled={disabled}
    className={`tv-btn tv-btn--primary ${className}`}
    style={{ width: '100%' }}
    aria-label="Start a new trip"
  >
    <PlusIcon width={15} height={15} />
    <span>New trip</span>
  </button>
);

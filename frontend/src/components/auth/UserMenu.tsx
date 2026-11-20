import React, { useState, useRef, useEffect } from 'react';
import { UserAvatar } from '../guide/UserAvatar';
import { useAuth } from '../../context/AuthContext';
import { LogOut, User as UserIcon } from 'lucide-react';

interface UserMenuProps {
  onNavigateProfile?: () => void;
}

export const UserMenu: React.FC<UserMenuProps> = ({ onNavigateProfile }) => {
  const { user, signOut } = useAuth();
  const [isDropdownOpen, setIsDropdownOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  if (!user) {
    return null;
  }

  const displayName =
    user.user_metadata?.full_name ||
    user.email?.split('@')[0] ||
    'VOYAGER';

  const avatarUrl = user.user_metadata?.avatar_url;

  const handleProfileClick = () => {
    setIsDropdownOpen(false);
    if (onNavigateProfile) {
      onNavigateProfile();
    }
  };

  return (
    <div className="relative" ref={dropdownRef}>
      {/* Clean User Trigger Badge (No Border, No Hover Transform) */}
      <button
        type="button"
        onClick={() => setIsDropdownOpen(!isDropdownOpen)}
        className="flex items-center gap-2 px-2 py-1.5 bg-transparent text-[#1F1E1E] border-0 outline-none rounded-none !transform-none hover:!transform-none active:!transform-none cursor-pointer select-none"
        aria-expanded={isDropdownOpen}
        aria-label="User Account Menu"
        style={{ transform: 'none' }}
      >
        <div className="w-5 h-5 bg-[#1F1E1E] text-white flex items-center justify-center text-[10px] font-black uppercase rounded-none shrink-0 overflow-hidden">
          <UserAvatar avatarUrl={avatarUrl} seed={user.id ?? user.email} size={20} name={displayName} />
        </div>
        <span className="text-xs font-black tracking-wider uppercase max-w-[120px] truncate font-body">
          {displayName}
        </span>
      </button>

      {/* Dropdown Panel */}
      {isDropdownOpen && (
        <div className="absolute right-0 mt-2 w-64 bg-white border border-[#D9D9D9] p-0 shadow-xl z-50 text-[#1F1E1E] rounded-none">
          {/* Header Info Banner */}
          <div className="p-3.5 bg-[#F9F9F9] border-b border-[#D9D9D9]">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 bg-[#1F1E1E] text-white flex items-center justify-center text-xs font-black uppercase rounded-none shrink-0 overflow-hidden">
                <UserAvatar avatarUrl={avatarUrl} seed={user.id ?? user.email} size={32} name={displayName} />
              </div>
              <div className="overflow-hidden">
                <p className="text-xs font-black uppercase tracking-wide truncate font-body">{displayName}</p>
                <p className="text-[11px] font-medium text-[#1F1E1E]/60 truncate font-body">{user.email}</p>
              </div>
            </div>
            <div className="mt-2.5 pt-2 border-t border-[#D9D9D9] flex items-center justify-between text-[9px] font-bold tracking-widest uppercase text-[#1F1E1E]/70 font-body">
              <span>VOYAGER MEMBER</span>
              <span className="text-emerald-600 font-extrabold">CLOUD ACTIVE</span>
            </div>
          </div>

          {/* Action Links */}
          <div className="p-1.5 flex flex-col gap-0.5">
            <button
              type="button"
              onClick={handleProfileClick}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-xs font-bold uppercase tracking-wider text-[#1F1E1E] hover:bg-[#F9F9F9] rounded-none transition-colors text-left cursor-pointer font-body"
            >
              <UserIcon className="w-4 h-4 text-[#1F1E1E]" />
              <span>Dedicated Profile</span>
            </button>
          </div>

          {/* Sign Out Action */}
          <div className="p-1.5 border-t border-[#D9D9D9]">
            <button
              type="button"
              onClick={async () => {
                setIsDropdownOpen(false);
                await signOut();
              }}
              className="w-full flex items-center gap-2.5 px-3 py-2 text-xs font-bold uppercase tracking-wider text-red-600 hover:bg-red-50 rounded-none transition-colors text-left cursor-pointer font-body"
            >
              <LogOut className="w-4 h-4" />
              <span>Sign Out</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

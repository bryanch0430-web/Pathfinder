/*
 * ProfilePage — v2 design system.
 *
 * Editorial account page: identity block, an account record set as a mono
 * key/value ledger, and a link to the real trip library. Same tokens, type and hairlines
 * as the marketing surface at the app surface's tighter density.
 */
import { UserAvatar } from '../components/guide/UserAvatar';
import React, { useEffect, useRef, useState } from 'react';
import { useGSAP } from '@gsap/react';
import gsap from 'gsap';

import { useAuth } from '../context/AuthContext';
import { supabase } from '../lib/supabase';
import { uploadAvatar } from '../services/uploadService';
import { AppBar } from '../components/common/AppBar';
import { AdminSettings } from './AdminSettings';
import { apiFetch } from '../services/apiClient';
import type { TripModelResponse } from '../services/tripService';
import {
  ArrowUpRightIcon,
  CheckIcon,
  ClockIcon,
  CompassIcon,
  GraphIcon,
  PlusIcon,
} from '../components/home/v2/IconsV2';
import { EASE, prefersReducedMotion, splitLines } from '../components/home/v2/motion';

interface ProfilePageProps {
  onNavigateHome: () => void;
  onNavigateExplore?: () => void;
  onStartPlanning?: () => void;
  onNavigateTrips?: () => void;
}

export const ProfilePage: React.FC<ProfilePageProps> = ({
  onNavigateHome,
  onNavigateExplore,
  onStartPlanning,
  onNavigateTrips,
}) => {
  const { user, signOut, updateProfile } = useAuth();
  const root = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLHeadingElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [displayName, setDisplayName] = useState(
    user?.user_metadata?.full_name || user?.email?.split('@')[0] || 'Traveller',
  );
  const [avatarUrl, setAvatarUrl] = useState<string>(
    user?.user_metadata?.avatar_url || '',
  );
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isSaved, setIsSaved] = useState(false);
  const [tripCount, setTripCount] = useState<number | null>(null);

  const email = user?.email || 'Not signed in';
  const initials = displayName.trim().slice(0, 2).toUpperCase();
  const userId = user?.id || 'guest-session';
  const isGuest = !user;

  useEffect(() => {
    setDisplayName(user?.user_metadata?.full_name || user?.email?.split('@')[0] || 'Traveller');
    setAvatarUrl(user?.user_metadata?.avatar_url || '');
  }, [user?.id, user?.user_metadata?.full_name, user?.user_metadata?.avatar_url, user?.email]);

  useEffect(() => {
    let active = true;
    const loadTrips = () => {
      void apiFetch<TripModelResponse[]>('/api/trips', { method: 'GET' }).then((response) => {
        if (active && response.ok && response.data) setTripCount(response.data.length);
      });
    };
    loadTrips();
    window.addEventListener('tripverse:trips-claimed', loadTrips);
    return () => {
      active = false;
      window.removeEventListener('tripverse:trips-claimed', loadTrips);
    };
  }, [user?.id]);

  useGSAP(
    () => {
      if (prefersReducedMotion()) return;

      const split = titleRef.current ? splitLines(titleRef.current) : null;
      const tl = gsap.timeline({ defaults: { ease: EASE } });

      tl.from('.tv-profile__eyebrow', { y: 14, opacity: 0, duration: 0.7 });
      if (split) tl.from(split.lines, { yPercent: 115, duration: 1.05, stagger: 0.08 }, '-=0.45');
      tl.from('.tv-profile__identity', { y: 24, opacity: 0, duration: 0.9 }, '-=0.6')
        .from('.tv-profile__card', {
          clipPath: 'inset(0% 0% 100% 0%)',
          y: 28,
          opacity: 0,
          duration: 1.1,
          stagger: 0.09,
        }, '-=0.6');

      return () => split?.revert();
    },
    { scope: root },
  );

  const handleAvatarFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    e.target.value = ''; // let the same file be re-picked

    setIsUploading(true);
    setUploadError(null);

    if (!user) {
      setUploadError('Sign in to update your profile photo.');
      setIsUploading(false);
      return;
    }

    const { url, error } = await uploadAvatar(file);

    if (error || !url) {
      setUploadError(error || 'That photo could not be uploaded.');
      setIsUploading(false);
      return;
    }

    try {
      if (!supabase) throw new Error('Photo upload needs Supabase; this local account keeps the generated avatar.');
      const { error: updateError } = await supabase.auth.updateUser({ data: { avatar_url: url } });
      if (updateError) setUploadError(updateError.message);
      else setAvatarUrl(url);
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : 'Could not save your profile photo.');
    } finally {
      setIsUploading(false);
    }
  };

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaveError(null);
    setIsSaved(false);
    const fullName = displayName.trim();
    if (!fullName) {
      setSaveError('Enter a display name.');
      return;
    }
    setIsSaving(true);
    try {
      const { error } = await updateProfile(fullName);
      if (error) setSaveError(error.message);
      else setIsSaved(true);
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Could not save your details.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleSignOut = async () => {
    const { error } = await signOut();
    if (error) setSaveError(error.message);
    else onNavigateHome();
  };

  return (
    <div className="tv2 tv2-app" ref={root}>
      <AppBar
        onNavigateHome={onNavigateHome}
        onStartPlanning={onStartPlanning}
        onNavigateExplore={onNavigateExplore}
        onNavigateTrips={onNavigateTrips}
        current="profile"
      />

      <main className="tv-container">
        <header className="tv-page__head">
          <span className="tv-eyebrow tv-profile__eyebrow">Account</span>
          <h1 className="tv-display tv-page__title" ref={titleRef}>
            {displayName}
          </h1>
          {user?.user_metadata?.is_admin ? (
            <p className="tv-meta" style={{ marginTop: '.4rem', letterSpacing: '.14em', textTransform: 'uppercase' }}>
              Administrator
            </p>
          ) : null}

          <div className="tv-profile__identity">
            <button
              type="button"
              className="tv-avatar"
              onClick={() => fileInputRef.current?.click()}
              aria-label="Change profile photo"
              disabled={isUploading}
            >
              {avatarUrl ? (
                <img src={avatarUrl} alt="" className="tv-avatar__img" />
              ) : (
                <UserAvatar seed={user?.id ?? user?.email} size={68} />
              )}
              <span className="tv-avatar__edit">
                {isUploading ? <ClockIcon width={15} height={15} /> : <PlusIcon width={15} height={15} />}
              </span>
            </button>

            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              hidden
              onChange={handleAvatarFileChange}
            />

            <div className="tv-profile__identity-meta">
              <p className="tv-meta">{email}</p>
              <span className={`tv-tag ${isGuest ? '' : 'tv-tag--green'}`}>
                {isGuest ? 'Guest session' : 'Signed in'}
              </span>
            </div>
          </div>

          {uploadError && (
            <div className="tv-alert" role="alert" style={{ marginTop: '1.25rem' }}>
              {uploadError}
            </div>
          )}
        </header>

        <div className="tv-profile__grid tv-collapse">
          {/* Editable details */}
          <section className="tv-card tv-profile__card">
            <div className="tv-profile__card-head">
              <GraphIcon width={18} height={18} />
              <h2 className="tv-label">Your details</h2>
            </div>

            <form onSubmit={handleSaveProfile} className="tv-profile__form">
              {saveError && <div className="tv-alert" role="alert">{saveError}</div>}
              <div className="tv-field">
                <label htmlFor="pf-name" className="tv-field__label">Display name</label>
                <input
                  id="pf-name"
                  className="tv-input"
                  value={displayName}
                  onChange={(e) => setDisplayName(e.target.value)}
                  placeholder="How should we address you?"
                />
              </div>

              <div className="tv-field">
                <label htmlFor="pf-email" className="tv-field__label">Email</label>
                <input id="pf-email" className="tv-input" value={email} readOnly disabled />
                <span className="tv-meta" style={{ fontSize: '0.72rem' }}>
                  Change your email from your account provider.
                </span>
              </div>

              <button type="submit" className="tv-btn tv-btn--primary" disabled={isSaving} style={{ width: '100%' }}>
                {isSaved ? (
                  <>
                    <CheckIcon width={15} height={15} />
                    <span>Saved</span>
                  </>
                ) : (
                  <span>{isSaving ? 'Saving…' : 'Save changes'}</span>
                )}
              </button>
            </form>
          </section>

          {/* Account record */}
          <section className="tv-card tv-profile__card">
            <div className="tv-profile__card-head">
              <CompassIcon width={18} height={18} />
              <h2 className="tv-label">Account record</h2>
            </div>

            <dl className="tv-ledger__list">
              {[
                { k: 'Session', v: isGuest ? 'Guest' : 'Authenticated' },
                { k: 'User ID', v: `${userId.slice(0, 12)}${userId.length > 12 ? '…' : ''}` },
                { k: 'Journeys', v: tripCount === null ? '—' : String(tripCount) },
              ].map((row) => (
                <div key={row.k} className="tv-ledger__row">
                  <dt className="tv-meta">{row.k}</dt>
                  <dd className="tv-ledger__v">{row.v}</dd>
                </div>
              ))}
            </dl>

            <div className="tv-profile__actions">
              <button type="button" className="tv-btn tv-btn--ghost" onClick={onStartPlanning} style={{ width: '100%' }}>
                <span>Plan a new trip</span>
                <ArrowUpRightIcon width={15} height={15} />
              </button>
              <button type="button" className="tv-link tv-profile__signout" onClick={handleSignOut}>
                <span>{isGuest ? 'Sign in to save trips' : 'Sign out'}</span>
              </button>
            </div>
          </section>
        </div>

        {user?.user_metadata?.is_admin ? <AdminSettings /> : null}

        {/* Trip library entry */}
        <section className="tv-profile__trips" aria-labelledby="saved-trips">
          <div className="tv-sec__head tv-sec__head--row" style={{ marginBottom: '1.75rem' }}>
            <div>
              <span className="tv-label">Your collection</span>
              <h2 id="saved-trips" className="tv-display tv-profile__trips-title">
                Every journey has a place.
              </h2>
            </div>
            <button type="button" className="tv-link" onClick={onNavigateTrips}>
              <span>Open trip library</span>
              <ArrowUpRightIcon width={15} height={15} />
            </button>
          </div>
          <p className="tv-body">{tripCount === null ? 'Open your trip library to see plans in progress and finished itineraries.' : tripCount === 0 ? 'No journeys yet. Begin planning and they will appear here.' : `${tripCount} ${tripCount === 1 ? 'journey' : 'journeys'} ready to revisit in your trip library.`}</p>
        </section>
      </main>
    </div>
  );
};

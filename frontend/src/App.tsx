import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ScrollTrigger } from 'gsap/ScrollTrigger';
import { Companion } from './components/guide/Companion';
import { RouteCurtain, type RouteCurtainHandle } from './components/common/RouteCurtain';
import { HomeV2 } from './components/home/v2/HomeV2';
import { FooterV2 } from './components/home/v2/FooterV2';
import { useAuth } from './context/AuthContext';

// Home is the landing page and ships in the main bundle. Every other page loads on demand,
// so `/` doesn't pay for the planner, the studio or the chat's markdown renderer.
// `transitionTo` calls the same loaders as the curtain rises, so the chunk is usually in before the swap.
const LOADERS = {
  explore: () => import('./pages/Explore').then((m) => ({ default: m.Explore })),
  create: () => import('./pages/CreateTrip').then((m) => ({ default: m.CreateTrip })),
  login: () => import('./pages/LoginPage').then((m) => ({ default: m.LoginPage })),
  signup: () => import('./pages/SignupPage').then((m) => ({ default: m.SignupPage })),
  'reset-password': () => import('./pages/ResetPasswordPage').then((m) => ({ default: m.ResetPasswordPage })),
  profile: () => import('./pages/ProfilePage').then((m) => ({ default: m.ProfilePage })),
  trips: () => import('./pages/TripLibrary').then((m) => ({ default: m.TripLibrary })),
  guide: () => import('./pages/GuidePage').then((m) => ({ default: m.GuidePage })),
  credits: () => import('./pages/Credits').then((m) => ({ default: m.CreditsPage })),
};
const Explore = React.lazy(LOADERS.explore);
const CreateTrip = React.lazy(LOADERS.create);
const LoginPage = React.lazy(LOADERS.login);
const SignupPage = React.lazy(LOADERS.signup);
const ResetPasswordPage = React.lazy(LOADERS['reset-password']);
const ProfilePage = React.lazy(LOADERS.profile);
const TripLibrary = React.lazy(LOADERS.trips);
const GuidePage = React.lazy(LOADERS.guide);
const CreditsPage = React.lazy(LOADERS.credits);

type ViewState = 'home' | keyof typeof LOADERS;

const VIEW_LABELS: Record<ViewState, string> = {
  home: 'Home',
  explore: 'Explore',
  create: 'Planner',
  login: 'Sign in',
  signup: 'Create account',
  'reset-password': 'Reset password',
  profile: 'Account',
  trips: 'Your trips',
  guide: 'Guide',
  credits: 'Credits',
};

const getViewFromPath = (): ViewState => {
  if (typeof window === 'undefined') return 'home';
  const path = window.location.pathname.toLowerCase();
  if (path === '/explore') return 'explore';
  if (path === '/create') return 'create';
  if (path === '/login') return 'login';
  if (path === '/signup') return 'signup';
  if (path === '/reset-password') return 'reset-password';
  if (path === '/profile') return 'profile';
  if (path === '/trips') return 'trips';
  if (path.startsWith('/trips/')) return 'create'; // Trip Studio lives inside the planner
  if (path === '/guide') return 'guide';
  if (path === '/credits') return 'credits';
  return 'home';
};

export const App: React.FC = () => {
  const [view, setView] = useState<ViewState>(getViewFromPath);
  const viewRef = useRef(view);
  viewRef.current = view;
  const { user, loading: authLoading } = useAuth();
  const curtain = useRef<RouteCurtainHandle>(null);

  // Every route change runs through the shared curtain transition.
  const transitionTo = useCallback((next: ViewState) => {
    const swap = () => {
      setView(next);
      // The outgoing page's pins and triggers are gone; re-measure the new one.
      requestAnimationFrame(() => ScrollTrigger.refresh());
    };
    if (next !== 'home') void LOADERS[next]();
    if (!curtain.current) return swap();
    curtain.current.play(VIEW_LABELS[next], swap);
  }, []);

  const navigateTo = (newView: ViewState) => {
    if (newView === view) return;
    const targetPath = newView === 'home' ? '/' : `/${newView}`;
    if (window.location.pathname !== targetPath) {
      window.history.pushState({}, '', targetPath);
    }
    transitionTo(newView);
  };

  useEffect(() => {
    // Chat <-> studio history steps stay inside the planner: no page curtain for them.
    const handlePopState = () => {
      const next = getViewFromPath();
      if (next !== viewRef.current) transitionTo(next);
    };
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, [transitionTo]);

  useEffect(() => {
    if (view === 'profile' && !authLoading && !user) {
      window.history.replaceState({}, '', '/login');
      transitionTo('login');
    }
  }, [view, authLoading, user, transitionTo]);

  return (
    <div className="min-h-screen antialiased">
      <RouteCurtain ref={curtain} />
      <React.Suspense fallback={null}>
        {view === 'home' && (
          <HomeV2
            onStartPlanning={() => navigateTo('create')}
            onNavigateExplore={() => navigateTo('explore')}
            onNavigateLogin={() => navigateTo('login')}
            onNavigateSignup={() => navigateTo('signup')}
            onNavigateProfile={() => navigateTo('profile')}
          />
        )}
        {view === 'explore' && (
          <Explore
            onStartPlanning={() => navigateTo('create')}
            onNavigateHome={() => navigateTo('home')}
            onNavigateProfile={() => navigateTo('profile')}
            onNavigateTrips={() => navigateTo('trips')}
          />
        )}
        {view === 'login' && (
          <LoginPage
            onNavigateSignup={() => navigateTo('signup')}
            onNavigateHome={() => navigateTo('home')}
            onSuccess={() => navigateTo('profile')}
          />
        )}
        {view === 'signup' && (
          <SignupPage
            onNavigateLogin={() => navigateTo('login')}
            onNavigateHome={() => navigateTo('home')}
            onSuccess={() => navigateTo('profile')}
          />
        )}
        {view === 'reset-password' && (
          <ResetPasswordPage
            onNavigateHome={() => navigateTo('home')}
            onNavigateLogin={() => navigateTo('login')}
            onSuccess={() => navigateTo('profile')}
          />
        )}
        {view === 'profile' && !authLoading && user && (
          <ProfilePage
            onNavigateHome={() => navigateTo('home')}
            onNavigateExplore={() => navigateTo('explore')}
            onStartPlanning={() => navigateTo('create')}
            onNavigateTrips={() => navigateTo('trips')}
          />
        )}
        {view === 'trips' && (
          <TripLibrary
            onNavigateHome={() => navigateTo('home')}
            onNavigateExplore={() => navigateTo('explore')}
            onNavigateProfile={() => navigateTo('profile')}
            onStartPlanning={() => navigateTo('create')}
            onOpenTrip={(tripId) => {
              localStorage.setItem('tripverse-active-session-id', `session-${tripId}`);
              navigateTo('create');
            }}
          />
        )}
        {view === 'guide' && (
          <GuidePage
            onNavigateHome={() => navigateTo('home')}
            onNavigateExplore={() => navigateTo('explore')}
            onNavigateProfile={() => navigateTo('profile')}
            onNavigateTrips={() => navigateTo('trips')}
            onStartPlanning={() => navigateTo('create')}
          />
        )}
        {view === 'credits' && (
          <CreditsPage
            onNavigateHome={() => navigateTo('home')}
            onNavigateExplore={() => navigateTo('explore')}
            onNavigateProfile={() => navigateTo('profile')}
            onNavigateTrips={() => navigateTo('trips')}
            onStartPlanning={() => navigateTo('create')}
          />
        )}
        {view === 'create' && (
          <CreateTrip
            onNavigateHome={() => navigateTo('home')}
            onNavigateExplore={() => navigateTo('explore')}
            onNavigateProfile={() => navigateTo('profile')}
            onNavigateLogin={() => navigateTo('login')}
            onNavigateSignup={() => navigateTo('signup')}
          />
        )}
      </React.Suspense>
      {/* One footer for every page. Home renders its own (it scrolls to its sections); the planner
          is a full-height workspace with nowhere for a footer to go. */}
      {view !== 'home' && view !== 'create' && (
        <div className="tv2 tv-footer-shell">
          <FooterV2 onStartPlanning={() => navigateTo('create')} onNavigateExplore={() => navigateTo('explore')} />
        </div>
      )}
      <Companion view={view} />
    </div>
  );
};

export default App;

/*
 * Explore: places to start from. The page itself is `ExploreIndex`; every place
 * opens the planner with its prompt typed in, ready to send (see `planFrom`).
 */
import { AppBar } from '../components/common/AppBar';
import { useSmoothScroll } from '../hooks/useSmoothScroll';
import { ExploreIndex } from './explore/ExploreIndex';
import '../styles/explore-index.css';

interface ExploreProps {
  onStartPlanning: () => void;
  onNavigateHome: () => void;
  onNavigateProfile?: () => void;
  onNavigateTrips?: () => void;
}

export function Explore({ onStartPlanning, onNavigateHome, onNavigateProfile, onNavigateTrips }: ExploreProps) {
  useSmoothScroll(true);

  return (
    <div className="tv2 tv2-app xp">
      <AppBar
        onNavigateHome={onNavigateHome}
        onNavigateProfile={onNavigateProfile}
        onNavigateTrips={onNavigateTrips}
        onStartPlanning={onStartPlanning}
        current="explore"
      />
      <ExploreIndex onStartPlanning={onStartPlanning} />
    </div>
  );
}

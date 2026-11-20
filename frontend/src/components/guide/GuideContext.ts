import { createContext, useContext } from 'react';
import { DEFAULT_GUIDE, type Guide } from './guides';

/** The active trip's guide, so every avatar in the chat is the same character. */
export const GuideContext = createContext<Guide>(DEFAULT_GUIDE);
export const useGuide = () => useContext(GuideContext);

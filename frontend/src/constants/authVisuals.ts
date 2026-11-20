export interface AuthVisualItem {
  id: string;
  name: string;
  destination: string;
  location: string;
  experience: string;
  imageSrc: string;
  objectPosition: string;
  expeditionTag: string;
  voyageTag: string;
}

/**
 * Photography for the authentication panes.
 *
 * Captions were previously mislabelled — four frames claimed Kyoto, Porto,
 * the Amalfi Coast and the Dolomites while showing Lisbon trams and an
 * unidentified mountain range. They now describe what is actually in each
 * photograph. Six of the eight are Lisbon's Carris tram network, which the
 * livery and architecture make unmistakable; the remaining two are described
 * by subject rather than assigned a location that cannot be verified.
 */
export const AUTH_VISUALS: AuthVisualItem[] = [
  {
    id: 'lisbon-tram-cobbles',
    name: 'Carris tram on cobbles',
    destination: 'LISBON',
    location: 'PORTUGAL',
    experience: 'Remodelado trams working the cobbled hill routes',
    imageSrc: '/assets/auth/pexels-ugurcan-ozmen-61083217-28553917.jpg',
    objectPosition: 'center 45%',
    expeditionTag: 'Frame 01',
    voyageTag: 'Pathfinder',
  },
  {
    id: 'lisbon-camoes',
    name: 'Tram at Luís de Camões',
    destination: 'LISBON',
    location: 'PRAÇA LUÍS DE CAMÕES, PORTUGAL',
    experience: 'The Chiado terminus in full afternoon light',
    imageSrc: '/assets/auth/pexels-marceloverfe-16338751.jpg',
    objectPosition: 'center 35%',
    expeditionTag: 'Frame 02',
    voyageTag: 'Pathfinder',
  },
  {
    id: 'lisbon-reflection',
    name: 'Tram mirrored in rainwater',
    destination: 'LISBON',
    location: 'BAIXA, PORTUGAL',
    experience: 'A yellow tram doubled in standing rainwater',
    imageSrc: '/assets/auth/pexels-hikaique-1563234.jpg',
    objectPosition: 'center 50%',
    expeditionTag: 'Frame 03',
    voyageTag: 'Pathfinder',
  },
  {
    id: 'lisbon-santa-catarina',
    name: 'Tram descending Santa Catarina',
    destination: 'LISBON',
    location: 'SANTA CATARINA, PORTUGAL',
    experience: 'Pastel facades along the descent to the river',
    imageSrc: '/assets/auth/pexels-popovkin-27401407.jpg',
    objectPosition: 'center 40%',
    expeditionTag: 'Frame 04',
    voyageTag: 'Pathfinder',
  },
  {
    id: 'lisbon-carreira-28',
    name: 'Carreira 28',
    destination: 'LISBON',
    location: 'ROUTE 28, PORTUGAL',
    experience: 'The 28 working its way through the old town',
    imageSrc: '/assets/auth/pexels-815774834-19259314.jpg',
    objectPosition: 'center 45%',
    expeditionTag: 'Frame 05',
    voyageTag: 'Pathfinder',
  },
  {
    id: 'lisbon-plaza',
    name: 'Tram crossing the plaza',
    destination: 'LISBON',
    location: 'PORTUGAL',
    experience: 'Overhead lines and tiled facades above the tracks',
    imageSrc: '/assets/auth/pexels-cruz-in-portugal-22037151.jpg',
    objectPosition: 'center 40%',
    expeditionTag: 'Frame 06',
    voyageTag: 'Pathfinder',
  },
  {
    id: 'mountain-range',
    name: 'Snow-capped range',
    destination: 'HIGH COUNTRY',
    location: 'ALPINE RANGE',
    experience: 'Cloud breaking over a snow-dusted ridgeline',
    imageSrc: '/assets/auth/pexels-lorenzomessinaph-34666834.jpg',
    objectPosition: 'center 50%',
    expeditionTag: 'Frame 07',
    voyageTag: 'Pathfinder',
  },
  {
    id: 'domed-facade',
    name: 'Domed civic facade',
    destination: 'OLD QUARTER',
    location: 'HISTORIC CENTRE',
    experience: 'A copper dome and turret against hard blue sky',
    imageSrc: '/assets/auth/pexels-zeynep-yilmaz-327514331-31922059.jpg',
    objectPosition: 'center 40%',
    expeditionTag: 'Frame 08',
    voyageTag: 'Pathfinder',
  },
];

export const SIGNUP_AUTH_VISUAL: AuthVisualItem = AUTH_VISUALS[1];

export const LOGIN_AUTH_VISUAL: AuthVisualItem = AUTH_VISUALS[4];

export const DEFAULT_AUTH_VISUAL: AuthVisualItem = SIGNUP_AUTH_VISUAL;

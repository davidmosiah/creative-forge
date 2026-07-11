export type VideoFormat = 'story' | 'portrait' | 'square';
export type SceneLayout = 'center' | 'bottom' | 'split' | 'full-bleed';
export type SceneEnter = 'cut' | 'fade' | 'rise';

export type CreativeAsset = {
  kind: 'image' | 'video';
  path: string;
  fit: 'contain' | 'cover';
};

export type CreativeScene = {
  id: string;
  startFrame: number;
  durationInFrames: number;
  layout: SceneLayout;
  background: string;
  foreground: string;
  accent: string;
  ctaForeground?: string;
  enter: SceneEnter;
  asset: string | null;
  text: {
    eyebrow?: string;
    headline?: string;
    body?: string;
    cta?: string;
  };
};

export type CreativeAudioTrack = {
  kind: 'music' | 'voiceover';
  path: string;
  volume: number;
};

export type CreativeCaption = {
  startFrame: number;
  durationInFrames: number;
  text: string;
};

export type CreativeVideoProps = {
  app: {slug: string; name: string};
  brand: {
    palette: Record<string, string>;
    fonts: Record<string, string>;
  };
  locale: string;
  copyLanguage: string;
  format: VideoFormat;
  width: number;
  height: number;
  fps: 30;
  durationInFrames: number;
  safeZones: {
    topRatio: number;
    bottomRatio: number;
    topPixels: number;
    bottomPixels: number;
    minimum: {top?: number; bottom?: number};
  };
  assets: Record<string, CreativeAsset>;
  audioTracks: CreativeAudioTrack[];
  captions: CreativeCaption[];
  muted: boolean;
  scenes: CreativeScene[];
};

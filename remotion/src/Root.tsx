import {Composition} from 'remotion';
import {CreativeVideo} from './CreativeVideo';
import type {CreativeVideoProps} from './types';

const defaultProps: CreativeVideoProps = {
  app: {slug: 'preview', name: 'Creative Forge'},
  brand: {
    palette: {accent: '#6f3e24'},
    fonts: {headline: 'Georgia', body: 'Arial'},
  },
  locale: 'en-US',
  copyLanguage: 'en',
  format: 'story',
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 450,
  safeZones: {
    topRatio: 0.14,
    bottomRatio: 0.2,
    topPixels: 269,
    bottomPixels: 384,
    minimum: {top: 0.14, bottom: 0.2},
  },
  assets: {},
  audioTracks: [],
  captions: [],
  muted: true,
  scenes: [
    {
      id: 'preview',
      startFrame: 0,
      durationInFrames: 450,
      layout: 'center',
      background: '#fff3d6',
      foreground: '#2d2118',
      accent: '#6f3e24',
      enter: 'fade',
      asset: null,
      text: {headline: 'Agent-authored scene plan'},
    },
  ],
};

export const RemotionRoot = () => (
  <Composition
    id="CreativeVideo"
    component={CreativeVideo}
    durationInFrames={defaultProps.durationInFrames}
    fps={30}
    width={defaultProps.width}
    height={defaultProps.height}
    defaultProps={defaultProps}
    calculateMetadata={({props}) => ({
      durationInFrames: props.durationInFrames,
      fps: 30,
      width: props.width,
      height: props.height,
    })}
  />
);

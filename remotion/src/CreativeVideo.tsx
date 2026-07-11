import type {CSSProperties, FC} from 'react';
import {
  AbsoluteFill,
  Html5Audio,
  Img,
  interpolate,
  OffthreadVideo,
  Sequence,
  staticFile,
  useCurrentFrame,
} from 'remotion';
import type {
  CreativeAsset,
  CreativeCaption,
  CreativeScene,
  CreativeVideoProps,
} from './types';

const layoutStyle = (layout: CreativeScene['layout']): CSSProperties => {
  if (layout === 'bottom') {
    return {justifyContent: 'flex-end', textAlign: 'center'};
  }
  if (layout === 'split') {
    return {justifyContent: 'space-between', textAlign: 'left'};
  }
  if (layout === 'full-bleed') {
    return {justifyContent: 'center', textAlign: 'left'};
  }
  return {justifyContent: 'center', textAlign: 'center'};
};

const SceneAsset: FC<{asset: CreativeAsset | undefined}> = ({asset}) => {
  if (!asset) return null;
  const style: CSSProperties = {
    width: '100%',
    maxHeight: '52%',
    objectFit: asset.fit,
    borderRadius: 32,
  };
  if (asset.kind === 'video') {
    return <OffthreadVideo src={staticFile(asset.path)} style={style} muted />;
  }
  return <Img src={staticFile(asset.path)} style={style} />;
};

const SceneLayer: FC<{
  scene: CreativeScene;
  asset: CreativeAsset | undefined;
  props: CreativeVideoProps;
}> = ({scene, asset, props}) => {
  const frame = useCurrentFrame();
  const progress = interpolate(frame, [0, 12], [0, 1], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  const opacity = scene.enter === 'cut' ? 1 : progress;
  const translateY = scene.enter === 'rise' ? (1 - progress) * 48 : 0;
  return (
    <AbsoluteFill
      style={{
        ...layoutStyle(scene.layout),
        boxSizing: 'border-box',
        display: 'flex',
        flexDirection: scene.layout === 'split' ? 'row' : 'column',
        gap: 42,
        paddingTop: props.safeZones.topPixels,
        paddingBottom: props.safeZones.bottomPixels,
        paddingLeft: Math.round(props.width * 0.075),
        paddingRight: Math.round(props.width * 0.075),
        background: scene.background,
        color: scene.foreground,
        fontFamily: props.brand.fonts.body,
        opacity,
        transform: `translateY(${translateY}px)`,
      }}
    >
      <SceneAsset asset={asset} />
      <div style={{display: 'flex', flexDirection: 'column', gap: 24}}>
        {scene.text.eyebrow ? (
          <div
            style={{
              color: scene.accent,
              fontSize: 34,
              fontWeight: 700,
              letterSpacing: 4,
              textTransform: 'uppercase',
            }}
          >
            {scene.text.eyebrow}
          </div>
        ) : null}
        {scene.text.headline ? (
          <div
            style={{
              fontFamily: props.brand.fonts.headline,
              fontSize: props.format === 'story' ? 82 : 68,
              fontWeight: 700,
              lineHeight: 1.04,
            }}
          >
            {scene.text.headline}
          </div>
        ) : null}
        {scene.text.body ? (
          <div style={{fontSize: 42, lineHeight: 1.3}}>{scene.text.body}</div>
        ) : null}
        {scene.text.cta ? (
          <div
            style={{
              alignSelf: scene.layout === 'center' ? 'center' : 'flex-start',
              marginTop: 16,
              padding: '22px 36px',
              borderRadius: 999,
              background: scene.accent,
              color:
                scene.ctaForeground ??
                props.brand.palette.on_accent ??
                '#ffffff',
              fontSize: 36,
              fontWeight: 800,
            }}
          >
            {scene.text.cta}
          </div>
        ) : null}
      </div>
    </AbsoluteFill>
  );
};

const CaptionLayer: FC<{
  caption: CreativeCaption;
  props: CreativeVideoProps;
}> = ({caption, props}) => (
  <AbsoluteFill
    style={{
      alignItems: 'center',
      boxSizing: 'border-box',
      display: 'flex',
      justifyContent: 'flex-end',
      paddingBottom: props.safeZones.bottomPixels + 24,
      paddingLeft: Math.round(props.width * 0.08),
      paddingRight: Math.round(props.width * 0.08),
      pointerEvents: 'none',
    }}
  >
    <div
      style={{
        background: 'rgba(0, 0, 0, 0.78)',
        borderRadius: 18,
        color: '#ffffff',
        fontFamily: props.brand.fonts.body,
        fontSize: props.format === 'story' ? 44 : 36,
        fontWeight: 700,
        lineHeight: 1.2,
        maxWidth: '92%',
        padding: '14px 22px',
        textAlign: 'center',
        whiteSpace: 'pre-line',
      }}
    >
      {caption.text}
    </div>
  </AbsoluteFill>
);

export const CreativeVideo: FC<CreativeVideoProps> = (props) => (
  <AbsoluteFill>
    {props.scenes.map((scene) => (
      <Sequence
        key={scene.id}
        name={scene.id}
        from={scene.startFrame}
        durationInFrames={scene.durationInFrames}
      >
        <SceneLayer
          scene={scene}
          asset={scene.asset ? props.assets[scene.asset] : undefined}
          props={props}
        />
      </Sequence>
    ))}
    {props.captions.map((caption, index) => (
      <Sequence
        key={`${caption.startFrame}-${index}`}
        name={`caption-${index + 1}`}
        from={caption.startFrame}
        durationInFrames={caption.durationInFrames}
      >
        <CaptionLayer caption={caption} props={props} />
      </Sequence>
    ))}
    {!props.muted
      ? props.audioTracks.map((track, index) => (
          <Html5Audio
            key={`${track.kind}-${track.path}-${index}`}
            src={staticFile(track.path)}
            volume={track.volume}
          />
        ))
      : null}
  </AbsoluteFill>
);

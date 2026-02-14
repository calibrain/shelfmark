import { ActivityVisualStatus } from './activityTypes';
import { getProgressConfig, isActiveDownloadStatus } from './activityStyles';

interface ActivityProgressBarProps {
  status: ActivityVisualStatus;
  progress?: number;
  animated?: boolean;
}

export const ActivityProgressBar = ({
  status,
  progress,
  animated,
}: ActivityProgressBarProps) => {
  if (!isActiveDownloadStatus(status)) {
    return null;
  }

  const config = getProgressConfig(status, progress);

  return (
    <div className="h-1.5 bg-gray-200 dark:bg-gray-700 overflow-hidden relative">
      <div
        className={`h-full ${config.color} transition-all duration-300 relative overflow-hidden`}
        style={{ width: `${Math.min(100, Math.max(0, config.percent))}%` }}
      >
        {(animated ?? config.animated) && config.percent < 100 && (
          <span
            className="absolute inset-0 opacity-30 activity-wave"
            style={{
              background:
                'linear-gradient(90deg, transparent 0%, rgba(255, 255, 255, 0.55) 50%, transparent 100%)',
              backgroundSize: '200% 100%',
            }}
          />
        )}
      </div>
    </div>
  );
};

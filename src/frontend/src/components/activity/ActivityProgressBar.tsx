import { getProgressConfig, isActiveDownloadStatus } from './activityStyles';
import { ActivityVisualStatus } from './activityTypes';

interface ActivityProgressBarProps {
  status: ActivityVisualStatus;
  progress?: number;
  animated?: boolean;
}

export const ActivityProgressBar = ({ status, progress, animated }: ActivityProgressBarProps) => {
  if (!isActiveDownloadStatus(status)) {
    return null;
  }

  const config = getProgressConfig(status, progress);

  return (
    <div className="relative h-1.5 overflow-hidden bg-gray-200 dark:bg-gray-700">
      <div
        className={`h-full ${config.color} relative overflow-hidden transition-all duration-300`}
        style={{ width: `${Math.min(100, Math.max(0, config.percent))}%` }}
      >
        {(animated ?? config.animated) && config.percent < 100 && (
          <span
            className="activity-wave absolute inset-0 opacity-30"
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

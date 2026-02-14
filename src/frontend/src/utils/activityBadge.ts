export interface ActivityStatusCounts {
  ongoing: number;
  completed: number;
  errored: number;
  pendingRequests: number;
}

export interface ActivityBadgeState {
  total: number;
  colorClass: string;
  title: string;
}

export const getActivityBadgeState = (
  statusCounts: ActivityStatusCounts,
  isAdmin: boolean
): ActivityBadgeState | null => {
  const pendingRequests = isAdmin ? statusCounts.pendingRequests : 0;
  const total = statusCounts.ongoing + statusCounts.completed + statusCounts.errored + pendingRequests;

  if (total <= 0) {
    return null;
  }

  let colorClass = 'bg-green-500';
  if (statusCounts.errored > 0) {
    colorClass = 'bg-red-500';
  } else if (statusCounts.ongoing > 0) {
    colorClass = 'bg-blue-500';
  } else if (pendingRequests > 0) {
    colorClass = 'bg-amber-500';
  }

  const title = isAdmin
    ? `${statusCounts.ongoing} ongoing, ${statusCounts.completed} completed, ${statusCounts.errored} failed, ${pendingRequests} pending requests`
    : `${statusCounts.ongoing} ongoing, ${statusCounts.completed} completed, ${statusCounts.errored} failed`;

  return {
    total,
    colorClass,
    title,
  };
};

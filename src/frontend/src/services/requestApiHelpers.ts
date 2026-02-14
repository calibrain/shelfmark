import { RequestRecord } from '../types';

export interface RequestListParams {
  status?: RequestRecord['status'];
  limit?: number;
  offset?: number;
}

export interface FulfilAdminRequestBody {
  release_data?: Record<string, unknown>;
  admin_note?: string;
}

export interface RejectAdminRequestBody {
  admin_note?: string;
}

export const buildRequestListUrl = (
  baseUrl: string,
  params: RequestListParams = {}
): string => {
  const query = new URLSearchParams();
  if (params.status) {
    query.set('status', params.status);
  }
  if (typeof params.limit === 'number') {
    query.set('limit', String(params.limit));
  }
  if (typeof params.offset === 'number') {
    query.set('offset', String(params.offset));
  }

  const queryString = query.toString();
  return queryString ? `${baseUrl}?${queryString}` : baseUrl;
};

export const buildAdminRequestActionUrl = (
  adminRequestsBaseUrl: string,
  id: number,
  action: 'fulfil' | 'reject'
): string => {
  return `${adminRequestsBaseUrl}/${encodeURIComponent(String(id))}/${action}`;
};

export const buildFulfilAdminRequestBody = (
  body: FulfilAdminRequestBody = {}
): FulfilAdminRequestBody => {
  return {
    release_data: body.release_data,
    admin_note: body.admin_note,
  };
};

export const buildRejectAdminRequestBody = (
  body: RejectAdminRequestBody = {}
): RejectAdminRequestBody => {
  return {
    admin_note: body.admin_note,
  };
};

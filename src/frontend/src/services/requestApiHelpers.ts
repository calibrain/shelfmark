export interface FulfilAdminRequestBody {
  release_data?: Record<string, unknown>;
  admin_note?: string;
  manual_approval?: boolean;
}

export interface RejectAdminRequestBody {
  admin_note?: string;
}

export const buildFulfilBookRequestsUrl = (adminRequestsBaseUrl: string, bookId: number): string =>
  `${adminRequestsBaseUrl}/books/${encodeURIComponent(String(bookId))}/fulfil`;

export const buildFulfilAdminRequestBody = (
  body: FulfilAdminRequestBody = {},
): FulfilAdminRequestBody => {
  const payload: FulfilAdminRequestBody = {};
  if (body.release_data !== undefined) {
    payload.release_data = body.release_data;
  }
  if (body.admin_note !== undefined) {
    payload.admin_note = body.admin_note;
  }
  if (body.manual_approval !== undefined) {
    payload.manual_approval = body.manual_approval;
  }
  return payload;
};

export const buildRejectAdminRequestBody = (
  body: RejectAdminRequestBody = {},
): RejectAdminRequestBody => {
  return {
    admin_note: body.admin_note,
  };
};

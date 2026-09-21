const COMMIT_SHA = /[0-9a-f]{40}$/i;

/**
 * The short commit id a build was made from, or null when the build is unstamped.
 *
 * CI stamps BUILD_VERSION as `<yyyy-mm-dd>-<sha>` for published images and `pr-<sha>` for
 * PR images, so its first seven characters are the date ("2026-09"), not the commit.
 */
export const shortBuildId = (buildVersion?: string): string | null => {
  if (!buildVersion || buildVersion === 'N/A') {
    return null;
  }
  const sha = COMMIT_SHA.exec(buildVersion)?.[0];
  return (sha ?? buildVersion).slice(0, 7);
};

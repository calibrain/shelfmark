# Prioritize release discovery and available files on book detail

Status: ready-for-agent

## Problem

`Find another release` is buried in the advanced section even though it is a primary action. Available files and Send to Kindle can also be pushed below a long book description.

## Deliverable

Keep the existing book-detail design, but move `Find another release` to the top-level action area. Make Available files and Send to Kindle appear before descriptive content by moving `About this book` to the final section of the page.

Treat both adjustments as one small book-detail hierarchy change. Do not redesign the page or alter the underlying release-discovery and Kindle behavior.

## Acceptance criteria

- `Find another release` is visible without expanding Advanced.
- Available files and Send to Kindle precede `About this book` in the normal page order.
- `About this book` is the final book-detail section.
- Existing responsive layout, capability gates, and loading states continue to work.
- Relevant frontend checks pass.

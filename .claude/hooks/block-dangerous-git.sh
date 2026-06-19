#!/usr/bin/env bash
# Block dangerous git operations
# Only allows: git push origin feature/<name>, git branch -D feature/<name>

cmd="$1"

# Block force push to any branch
if echo "$cmd" | grep -qE 'git push .*--force'; then
  echo "BLOCKED: git push --force is not allowed"
  exit 1
fi

# Block push to main/master
if echo "$cmd" | grep -qE 'git push.*(master|main)'; then
  echo "BLOCKED: pushing to master/main is not allowed. Use feature branches."
  exit 1
fi

# Block git reset --hard (unless it's on a feature branch)
if echo "$cmd" | grep -qE 'git reset --hard'; then
  echo "BLOCKED: git reset --hard is not allowed"
  exit 1
fi

# Block git clean -fd
if echo "$cmd" | grep -qE 'git clean .*-fd'; then
  echo "BLOCKED: git clean -fd is not allowed"
  exit 1
fi

# Block deleting non-feature branches
if echo "$cmd" | grep -qE 'git branch -D (?!feature/)'; then
  if ! echo "$cmd" | grep -qE 'git branch -D feature/'; then
    echo "BLOCKED: deleting non-feature branches is not allowed"
    exit 1
  fi
fi

exit 0

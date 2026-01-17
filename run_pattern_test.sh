#!/bin/bash
# ==============================================================================
# run_pattern_test.sh - Test reversal patterns
# ==============================================================================
# Usage:
#   ./run_pattern_test.sh           # Run all patterns from 0 to 10
#   ./run_pattern_test.sh 3 7       # Run only pattern 3 and 7
#   ./run_pattern_test.sh 1 2 5 10  # Run pattern 1, 2, 5, and 10
# ==============================================================================

STRATEGY_FILE="src/strategy.py"

# Determine which pattern IDs to run
if [ $# -eq 0 ]; then
    PATTERN_IDS=(0 1 2 3 4 5 6 7 8 9 10)
else
    PATTERN_IDS=("$@")
fi

echo "Patterns to test: ${PATTERN_IDS[*]}"
echo ""

# Run for each pattern ID
for PATTERN_ID in "${PATTERN_IDS[@]}"; do
    echo "========================================"
    echo "Running with reversal_pattern_ids: [$PATTERN_ID]"
    echo "========================================"
    
    # Update the pattern ID in strategy.py
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/reversal_pattern_ids: List\[int\] = field(default_factory=lambda: \[[0-9]*\])/reversal_pattern_ids: List[int] = field(default_factory=lambda: [$PATTERN_ID])/" "$STRATEGY_FILE"
    else
        sed -i "s/reversal_pattern_ids: List\[int\] = field(default_factory=lambda: \[[0-9]*\])/reversal_pattern_ids: List[int] = field(default_factory=lambda: [$PATTERN_ID])/" "$STRATEGY_FILE"
    fi
    
    python3 src/main.py
    echo ""
done

# Restore to default [0]
if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s/reversal_pattern_ids: List\[int\] = field(default_factory=lambda: \[[0-9]*\])/reversal_pattern_ids: List[int] = field(default_factory=lambda: [0])/" "$STRATEGY_FILE"
else
    sed -i "s/reversal_pattern_ids: List\[int\] = field(default_factory=lambda: \[[0-9]*\])/reversal_pattern_ids: List[int] = field(default_factory=lambda: [0])/" "$STRATEGY_FILE"
fi

echo "Done! Restored reversal_pattern_ids to [0]"

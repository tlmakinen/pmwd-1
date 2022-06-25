# Parallelization
NUMBER_OF_MPI_LISTENERS_PER_NODE=2
MAX_NUMBER_OF_RANKS_WITH_SHARED_MEMORY=64

# Basic
PERIODIC
NTYPES=2

# Gravity
SELFGRAVITY
HIERARCHICAL_GRAVITY  # crucial for speed
FMM
MULTIPOLE_ORDER=5
EXTRA_HIGH_EWALD_ACCURACY
RANDOMIZE_DOMAINCENTER
NSOFTCLASSES=1

# Precision and Data Types
DOUBLEPRECISION=1
OUTPUT_IN_DOUBLEPRECISION
ENLARGE_DYNAMIC_RANGE_IN_TIME

# Output
ALLOW_HDF5_COMPRESSION

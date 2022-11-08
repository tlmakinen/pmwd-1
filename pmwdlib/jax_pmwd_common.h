#ifndef _JAX_PMWD_COMMON_H_
#define _JAX_PMWD_COMMON_H_

// This descriptor is common to both the jax_pmwd_cpu and jax_finufft_gpu modules
// We will use the jax_pmwd namespace for both

namespace jax_pmwd {

template <typename T>
struct PmwdDescriptor {
  T cell_size;
  int64_t n_particle;
  int64_t stride[3];
};
    
}

#endif

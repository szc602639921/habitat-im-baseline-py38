#!/bin/bash
#SBATCH --job-name=ddp_onav
#SBATCH --gres gpu:4
#SBATCH --nodes 1
#SBATCH --cpus-per-task 16
#SBATCH --ntasks-per-node 4
#SBATCH --output=slurm_logs/ddppo-%j.out
#SBATCH --error=slurm_logs/ddppo-%j.err

source /home/users/shen/miniconda3/bin/activate
conda activate habitat-web-py38

cd /home/users/shen/habitat-web-baselines

export GLOG_minloglevel=2
export MAGNUM_LOG=quiet

MASTER_ADDR=$(srun --ntasks=1 hostname 2>&1 | tail -n1)
export MASTER_ADDR

sensor=$1

set -x
srun python -u -m habitat_baselines.run \
    --exp-config habitat_baselines/config/objectnav/il_ddp_objectnav.yaml \
    --run-type train
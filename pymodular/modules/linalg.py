""" Specialized linear algebra modules """
import os
import sys
import glob
import warnings
import hashlib
from inspect import currentframe, getframeinfo

from pymodular import Module, DyadCarrier, LDAWrapper
import numpy as np
import scipy.sparse as sps
import scipy.linalg as spla  # Dense matrix solvers

from pymodular import SolverDenseLU, SolverDenseLDL, SolverDenseCholesky, SolverDiagonal, SolverDenseQR
from pymodular import matrix_is_symmetric, matrix_is_hermitian, matrix_is_diagonal
from pymodular import SolverSparseLU, SolverSparseCholeskyCVXOPT, SolverSparsePardiso, SolverSparseCholeskyScikit


def auto_determine_solver(A, isdiagonal=None, islowertriangular=None, isuppertriangular=None, ishermitian=None, issymmetric=None, ispositivedefinite=None):
    """
    Uses parts of Matlab's scheme https://nl.mathworks.com/help/matlab/ref/mldivide.html
    :param A: The matrix
    :param isdiagonal: Manual override for diagonal matrix
    :param islowertriangular: Override for lower triangular matrix
    :param isuppertriangular: Override for upper triangular matrix
    :param ishermitian: Override for hermitian matrix (prevents check)
    :param issymmetric: Override for symmetric matrix (prevents check). Is the same as hermitian for a real matrix
    :param ispositivedefinite: Manual override for positive definiteness
    :return: LinearSolver which should be 'best' for the matrix
    """
    issparse = sps.issparse(A)  # Check if the matrix is sparse
    issquare = A.shape[0] == A.shape[1]  # Check if the matrix is square

    if not issquare:
        if issparse:
            sps.SparseEfficiencyWarning("Only a dense version of QR solver is available")  # TODO
        return SolverDenseQR()

    # l_bw, u_bw = spla.bandwidth(A) # TODO Get bandwidth (implemented in scipy version > 1.8.0)

    if isdiagonal is None:  # Check if matrix is diagonal
        # TODO: This could be improved to check other sparse matrix types as well
        isdiagonal = matrix_is_diagonal(A)
    if isdiagonal:
        return SolverDiagonal()

    # Check if the matrix is triangular
    # TODO Currently only for dense matrices
    if islowertriangular is None:  # Check if matrix is lower triangular
        islowertriangular = False if issparse else np.allclose(A, np.tril(A))
    if islowertriangular:
        warnings.WarningMessage("Lower triangular solver not implemented", UserWarning, getframeinfo(currentframe()).filename, getframeinfo(currentframe()).lineno)

    if isuppertriangular is None:  # Check if matrix is upper triangular
        isuppertriangular = False if issparse else np.allclose(A, np.triu(A))
    if isuppertriangular:
        warnings.WarningMessage("Upper triangular solver not implemented", UserWarning, getframeinfo(currentframe()).filename, getframeinfo(currentframe()).lineno)

    ispermutedtriangular = False
    if ispermutedtriangular:
        warnings.WarningMessage("Permuted triangular solver not implemented", UserWarning, getframeinfo(currentframe()).filename, getframeinfo(currentframe()).lineno)

    # Check if the matrix is complex-valued
    iscomplex = np.iscomplexobj(A)
    if iscomplex:
        # Detect if the matrix is hermitian and/or symmetric
        if ishermitian is None:
            ishermitian = matrix_is_hermitian(A)
        if issymmetric is None:
            issymmetric = matrix_is_symmetric(A)
    else:
        if ishermitian is None and issymmetric is None:
            # Detect if the matrix is symmetric
            issymmetric = matrix_is_symmetric(A)
            ishermitian = issymmetric
        elif ishermitian is not None and issymmetric is not None:
            assert ishermitian == issymmetric, "For real-valued matrices, symmetry and hermitian must be equal"
        elif ishermitian is None:
            ishermitian = issymmetric
        elif issymmetric is None:
            issymmetric = ishermitian

    if issparse:
        # Prefer Intel Pardiso solver as it can solve any matrix TODO: Check for complex matrix
        if SolverSparsePardiso.defined:
            # TODO check for positive definiteness?  np.alltrue(A.diagonal() > 0) or np.alltrue(A.diagonal() < 0)
            return SolverSparsePardiso(symmetric=issymmetric, hermitian=ishermitian, positive_definite=ispositivedefinite)

        if ishermitian:
            # Check if diagonal is all positive or all negative -> Cholesky
            if np.alltrue(A.diagonal() > 0) or np.alltrue(A.diagonal() < 0):  # TODO what about the complex case?
                if SolverSparseCholeskyScikit.defined:
                    return SolverSparseCholeskyScikit()
                if SolverSparseCholeskyCVXOPT.defined:
                    return SolverSparseCholeskyCVXOPT()

        return SolverSparseLU()  # Default to LU, which should be possible for any non-singular square matrix

    else:  # Dense
        if ishermitian:
            # Check if diagonal is all positive or all negative
            if np.alltrue(A.diagonal() > 0) or np.alltrue(A.diagonal() < 0):
                return SolverDenseCholesky()
            else:
                return SolverDenseLDL(hermitian=ishermitian)
        elif issymmetric:
            return SolverDenseLDL(hermitian=ishermitian)
        else:
            # TODO: Detect if the matrix is Hessenberg
            return SolverDenseLU()


class LinSolve(Module):
    """ Linear solver module
    Solves linear system of equations Ax=b
    """
    def _prepare(self, dep_tol=1e-5, hermitian=None, symmetric=None, solver=None):
        """
        :param tol: Tolerance for detecting linear dependence of adjoint vector
        :param hermitian: Flag to omit the detection for hermitian matrix, saves some work for large matrices
        :param solver: Provide a custom LinearSolver to use that instead of the 'automatic' solver
        """
        self.dep_tol = dep_tol
        self.ishermitian = hermitian
        self.issymmetric = symmetric
        self.solver = solver

    def _response(self, mat, rhs):
        # Do some detections on the matrix type
        self.issparse = sps.issparse(mat)  # Check if it is a sparse matrix
        self.iscomplex = np.iscomplexobj(mat)  # Check if it is a complex-valued matrix
        if not self.iscomplex and self.issymmetric is not None:
            self.ishermitian = self.issymmetric
        if self.ishermitian is None:
            self.ishermitian = matrix_is_hermitian(mat)

        # Determine the solver we want to use
        if self.solver is None:
            self.solver = auto_determine_solver(mat, ishermitian=self.ishermitian)
        if not isinstance(self.solver, LDAWrapper):
            self.solver = LDAWrapper(self.solver)

        # Do factorication
        self.solver.update(mat)

        # Solution
        self.u = self.solver.solve(rhs)

        return self.u

    def _sensitivity(self, dfdv):
        mat, rhs = [s.state for s in self.sig_in]
        lam = self.solver.adjoint(dfdv)

        if self.issparse:
            if not self.iscomplex and (np.iscomplexobj(self.u) or np.iscomplexobj(lam)):
                warnings.warn("This one has not been checked yet!")  # TODO
                dmat = DyadCarrier([-np.real(lam), -np.imag(lam)], [np.real(self.u), np.imag(self.u)])
            else:
                dmat = DyadCarrier(-lam, self.u)
        else:
            if self.u.ndim > 1:
                dmat = np.einsum("iB,jB->ij", -lam, np.conj(self.u))
            else:
                dmat = np.outer(-lam, np.conj(self.u))
            if not self.iscomplex:
                dmat = np.real(dmat)

        db = np.real(lam) if np.isrealobj(rhs) else lam

        return dmat, db

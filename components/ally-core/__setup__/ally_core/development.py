'''
Created on Nov 24, 2011

@package: Newscoop
@copyright: 2011 Sourcefabric o.p.s.
@license: http://www.gnu.org/licenses/gpl-3.0.txt
@author: Gabriel Nistor

Provides the configurations for development tools.
'''

from .resource_manager import resourcesManager
from ally.container import ioc
from ally.core.impl.devel.memory_status import MemoryStatusPresenter

# --------------------------------------------------------------------
# Creating the development tools

@ioc.config
def application_mode() -> str:
    '''The application mode one of devel, prod'''
    return 'prod'


@ioc.entity
def memoryStatusPresenter():
    b = MemoryStatusPresenter()
    b.resourcesManager = resourcesManager()
    return b

@ioc.after(resourcesManager)
def development():
    if application_mode() == 'devel':
        memoryStatusPresenter()
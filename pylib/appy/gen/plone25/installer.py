'''This package contains stuff used at run-time for installing a generated
   Plone product.'''

# ------------------------------------------------------------------------------
import os, os.path, time
from StringIO import StringIO
from sets import Set
import appy
from appy.gen import Type, Ref
from appy.gen.po import PoParser
from appy.gen.utils import produceNiceMessage
from appy.gen.plone25.utils import updateRolesForPermission
from appy.shared.data import languages

class ZCTextIndexInfo:
    '''Silly class used for storing information about a ZCTextIndex.'''
    lexicon_id = "plone_lexicon"
    index_type = 'Okapi BM25 Rank'

class PloneInstaller:
    '''This Plone installer runs every time the generated Plone product is
       installed or uninstalled (in the Plone configuration interface).'''
    def __init__(self, reinstall, ploneSite, config):
        # p_cfg is the configuration module of the Plone product.
        self.reinstall = reinstall # Is it a fresh install or a re-install?
        self.ploneSite = ploneSite
        self.config = cfg = config
        # Unwrap some useful variables from config
        self.productName = cfg.PROJECTNAME
        self.minimalistPlone = cfg.minimalistPlone
        self.appClasses = cfg.appClasses
        self.appClassNames = cfg.appClassNames
        self.allClassNames = cfg.allClassNames
        self.catalogMap = cfg.catalogMap
        self.applicationRoles = cfg.applicationRoles # Roles defined in the app
        self.defaultAddRoles = cfg.defaultAddRoles
        self.workflows = cfg.workflows
        self.appFrontPage = cfg.appFrontPage
        self.showPortlet = cfg.showPortlet
        self.languages = cfg.languages
        self.languageSelector = cfg.languageSelector
        self.attributes = cfg.attributes
        # A buffer for logging purposes
        self.toLog = StringIO()
        self.typeAliases = {'sharing': '', 'gethtml': '',
            '(Default)': 'skynView', 'edit': 'skyn/edit',
            'index.html': '', 'properties': '', 'view': ''}
        self.tool = None # The Plone version of the application tool
        self.appyTool = None # The Appy version of the application tool
        self.toolName = '%sTool' % self.productName
        self.toolInstanceName = 'portal_%s' % self.productName.lower()

    @staticmethod
    def updateIndexes(ploneSite, indexInfo, logger):
        '''This method creates or updates, in a p_ploneSite, definitions of
           indexes in its portal_catalog, based on index-related information
           given in p_indexInfo. p_indexInfo is a dictionary of the form
           {s_indexName:s_indexType}. Here are some examples of index types:
           "FieldIndex", "ZCTextIndex", "DateIndex".'''
        catalog = ploneSite.portal_catalog
        zopeCatalog = catalog._catalog
        for indexName, indexType in indexInfo.iteritems():
            # If this index already exists but with a different type, remove it.
            if (indexName in zopeCatalog.indexes):
                oldType = zopeCatalog.indexes[indexName].__class__.__name__
                if oldType != indexType:
                    catalog.delIndex(indexName)
                    logger.info('Existing index "%s" of type "%s" was removed:'\
                                ' we need to recreate it with type "%s".' % \
                                (indexName, oldType, indexType))
            if indexName not in zopeCatalog.indexes:
                # We need to create this index
                if indexType != 'ZCTextIndex':
                    catalog.addIndex(indexName, indexType)
                else:
                    catalog.addIndex(indexName,indexType,extra=ZCTextIndexInfo)
                # Indexing database content based on this index.
                catalog.reindexIndex(indexName, ploneSite.REQUEST)
                logger.info('Created index "%s" of type "%s"...' % \
                            (indexName, indexType))

    actionsToHide = {
        'portal_actions': ('sitemap', 'accessibility', 'change_state','sendto'),
        'portal_membership': ('mystuff', 'preferences'),
        'portal_undo': ('undo',)
    }
    def customizePlone(self):
        '''Hides some UI elements that appear by default in Plone.'''
        for portalName, toHide in self.actionsToHide.iteritems():
            portal = getattr(self.ploneSite, portalName)
            portalActions = portal.listActions()
            for action in portalActions:
                if action.id in toHide: action.visible = False

    appyFolderType = 'AppyFolder'
    def registerAppyFolderType(self):
        '''We need a specific content type for the folder that will hold all
           objects created from this application, in order to remove it from
           Plone navigation settings. We will create a new content type based
           on Large Plone Folder.'''
        if not hasattr(self.ploneSite.portal_types, self.appyFolderType):
            portal_types = self.ploneSite.portal_types
            lpf = 'Large Plone Folder'
            largePloneFolder = getattr(portal_types, lpf)
            typeInfoName = 'ATContentTypes: ATBTreeFolder (ATBTreeFolder)'
            portal_types.manage_addTypeInformation(
                largePloneFolder.meta_type, id=self.appyFolderType,
                typeinfo_name=typeInfoName)
            appyFolder = getattr(portal_types, self.appyFolderType)
            appyFolder.title = 'Appy folder'
            #appyFolder.factory = largePloneFolder.factory
            #appyFolder.product = largePloneFolder.product
            # Copy actions and aliases
            appyFolder._actions = tuple(largePloneFolder._cloneActions())
            # Copy aliases from the base portal type
            appyFolder.setMethodAliases(largePloneFolder.getMethodAliases())
            # Prevent Appy folders to be visible in standard Plone navigation
            nv = self.ploneSite.portal_properties.navtree_properties
            metaTypesNotToList = list(nv.getProperty('metaTypesNotToList'))
            if self.appyFolderType not in metaTypesNotToList:
                metaTypesNotToList.append(self.appyFolderType)
            nv.manage_changeProperties(
                metaTypesNotToList=tuple(metaTypesNotToList))

    def getAddPermission(self, className):
        '''What is the name of the permission allowing to create instances of
           class whose name is p_className?'''
        return self.productName + ': Add ' + className

    def installRootFolder(self):
        '''Creates and/or configures, at the root of the Plone site and if
           needed, the folder where the application will store instances of
           root classes. Creates also the 'appy' folder (more precisely,
           a Filesystem Directory View) at the root of the site, for storing
           appy-wide ZPTs an images.'''
        # Register first our own Appy folder type if needed.
        site = self.ploneSite
        if not hasattr(site.portal_types, self.appyFolderType):
            self.registerAppyFolderType()
        # Create the folder
        if not hasattr(site.aq_base, self.productName):
            # Temporarily allow me to create Appy large plone folders
            getattr(site.portal_types, self.appyFolderType).global_allow = 1
            # Allow to create Appy large folders in the plone site
            getattr(site.portal_types,
                'Plone Site').allowed_content_types += (self.appyFolderType,)
            site.invokeFactory(self.appyFolderType, self.productName,
                               title=self.productName)
            getattr(site.portal_types, self.appyFolderType).global_allow = 0
            # Manager has been granted Add permissions for all root classes.
            # This may not be desired, so remove this.
            appFolder = getattr(site, self.productName)
            for className in self.config.rootClasses:
                permission = self.getAddPermission(className)
                appFolder.manage_permission(permission, (), acquire=0)
        else:
            appFolder = getattr(site, self.productName)
        # All roles defined as creators should be able to create the
        # corresponding root content types in this folder.
        i = -1
        allCreators = set()
        for klass in self.appClasses:
            i += 1
            if not klass.__dict__.has_key('root') or not klass.__dict__['root']:
                continue # It is not a root class
            creators = getattr(klass, 'creators', None)
            if not creators: creators = self.defaultAddRoles
            allCreators = allCreators.union(creators)
            className = self.appClassNames[i]
            permission = self.getAddPermission(className)
            updateRolesForPermission(permission, tuple(creators), appFolder)
        # Beyond content-type-specific "add" permissions, creators must also
        # have the main permission "Add portal content".
        permission = 'Add portal content'
        updateRolesForPermission(permission, tuple(allCreators), appFolder)
        # Creates the "appy" Directory view
        if hasattr(site.aq_base, 'skyn'):
            site.manage_delObjects(['skyn'])
        # This way, if Appy has moved from one place to the other, the
        # directory view will always refer to the correct place.
        addDirView = self.config.manage_addDirectoryView
        addDirView(site, appy.getPath() + '/gen/plone25/skin', id='skyn')

    def installTypes(self):
        '''Registers and configures the Plone content types that correspond to
           gen-classes.'''
        site = self.ploneSite
        # Do Plone-based type registration
        classes = self.config.listTypes(self.productName)
        self.config.installTypes(site, self.toLog, classes, self.productName)
        self.config.install_subskin(site, self.toLog, self.config.__dict__)
        # Set appy view/edit pages for every created type
        for className in self.allClassNames + ['%sTool' % self.productName]:
            # I did not put the app tool in self.allClassNames because it
            # must not be registered in portal_factory
            if hasattr(site.portal_types, className):
                # className may correspond to an abstract class that has no
                # corresponding Plone content type
                typeInfo = getattr(site.portal_types, className)
                typeInfo.setMethodAliases(self.typeAliases)
                # Update edit and view actions
                typeActions = typeInfo.listActions()
                for action in typeActions:
                    if action.id == 'view':
                        page = 'skynView'
                        action.edit(action='string:${object_url}/%s' % page)
                    elif action.id == 'edit':
                        page = 'skyn/edit'
                        action.edit(action='string:${object_url}/%s' % page)

        # Configure types for instance creation through portal_factory
        factoryTool = site.portal_factory
        factoryTypes = self.allClassNames + factoryTool.getFactoryTypes().keys()
        factoryTool.manage_setPortalFactoryTypes(listOfTypeIds=factoryTypes)

        # Configure CatalogMultiplex: tell what types will be catalogued or not.
        atTool = getattr(site, self.config.ARCHETYPETOOLNAME)
        for meta_type in self.catalogMap:
            submap = self.catalogMap[meta_type]
            current_catalogs = Set(
                [c.id for c in atTool.getCatalogsByType(meta_type)])
            if 'white' in submap:
                for catalog in submap['white']:
                    current_catalogs.update([catalog])
            if 'black' in submap:
                for catalog in submap['black']:
                    if catalog in current_catalogs:
                        current_catalogs.remove(catalog)
            atTool.setCatalogsByType(meta_type, list(current_catalogs))

    def updatePodTemplates(self):
        '''Creates or updates the POD templates in the tool according to pod
           declarations in the application classes.'''
        # Creates the templates for Pod fields if they do not exist.
        for contentType, appyTypes in self.attributes.iteritems():
            appyClass = self.tool.getAppyClass(contentType)
            if not appyClass: continue # May be an abstract class
            for appyType in appyTypes:
                if appyType.type != 'Pod': continue
                # Find the attribute that stores the template, and store on
                # it the default one specified in the appyType if no
                # template is stored yet.
                attrName = self.appyTool.getAttributeName(
                                        'podTemplate', appyClass, appyType.name)
                fileObject = getattr(self.appyTool, attrName)
                if not fileObject or (fileObject.size == 0):
                    # There is no file. Put the one specified in the appyType.
                    fileName = os.path.join(self.appyTool.getDiskFolder(),
                                            appyType.template)
                    if os.path.exists(fileName):
                        setattr(self.appyTool, attrName, fileName)
                        self.appyTool.log('Imported "%s" in the tool in ' \
                                          'attribute "%s"'% (fileName,attrName))
                    else:
                        self.appyTool.log('Template "%s" was not found!' % \
                                          fileName, type='error')

    def installTool(self):
        '''Configures the application tool.'''
        # Register the tool in Plone
        try:
            self.ploneSite.manage_addProduct[
                self.productName].manage_addTool(self.toolName)
        except self.config.BadRequest:
            # If an instance with the same name already exists, this error will
            # be unelegantly raised by Zope.
            pass
        except:
            e = sys.exc_info()
            if e[0] != 'Bad Request': raise
        
        # Hide the tool from the search form
        portalProperties = self.ploneSite.portal_properties
        if portalProperties is not None:
            siteProperties = getattr(portalProperties, 'site_properties', None)
            if siteProperties is not None and \
               siteProperties.hasProperty('types_not_searched'):
                current = list(siteProperties.getProperty('types_not_searched'))
                if self.toolName not in current:
                    current.append(self.toolName)
                    siteProperties.manage_changeProperties(
                        **{'types_not_searched' : current})

        # Hide the tool in the navigation
        if portalProperties is not None:
            nvProps = getattr(portalProperties, 'navtree_properties', None)
            if nvProps is not None and nvProps.hasProperty('idsNotToList'):
                current = list(nvProps.getProperty('idsNotToList'))
                if self.toolInstanceName not in current:
                    current.append(self.toolInstanceName)
                    nvProps.manage_changeProperties(**{'idsNotToList': current})

        self.tool = getattr(self.ploneSite, self.toolInstanceName)
        self.appyTool = self.tool.appy()
        if self.reinstall:
            self.tool.createOrUpdate(False, None)
        else:
            self.tool.createOrUpdate(True, None)

        self.updatePodTemplates()

        # Uncatalog tool
        self.tool.unindexObject()

        # Register tool as configlet
        portalControlPanel = self.ploneSite.portal_controlpanel
        portalControlPanel.unregisterConfiglet(self.toolName)
        portalControlPanel.registerConfiglet(
            self.toolName, self.productName,
            'string:${portal_url}/%s' % self.toolInstanceName, 'python:True',
            'Manage portal', # Access permission
            'Products', # Section to which the configlet should be added:
                        # (Plone, Products (default) or Member)
            1, # Visibility
            '%sID' % self.toolName, 'site_icon.gif', # Icon in control_panel
            self.productName, None)

    def installTranslations(self):
        '''Creates or updates the translation objects within the tool.'''
        translations = [t.o.id for t in self.appyTool.translations]
        # We browse the languages supported by this application and check
        # whether we need to create the corresponding Translation objects.
        for language in self.languages:
            if language in translations: continue
            # We will create, in the tool, the translation object for this
            # language. Determine first its title.
            langId, langEn, langNat = languages.get(language)
            if langEn != langNat:
                title = '%s (%s)' % (langEn, langNat)
            else:
                title = langEn
            self.appyTool.create('translations', id=language, title=title)
            self.appyTool.log('Translation object created for "%s".' % language)
        # Now, we synchronise every Translation object with the corresponding
        # "po" file on disk.
        appFolder = self.config.diskFolder
        appName = self.config.PROJECTNAME
        dn = os.path.dirname
        jn = os.path.join
        i18nFolder = jn(jn(jn(dn(dn(dn(appFolder))),'Products'),appName),'i18n')
        for translation in self.appyTool.translations:
            # Get the "po" file
            poName = '%s-%s.po' % (appName, translation.id)
            poFile = PoParser(jn(i18nFolder, poName)).parse()
            for message in poFile.messages:
                setattr(translation, message.id, message.getMessage())
            self.appyTool.log('Translation "%s" updated from "%s".' % \
                              (translation.id, poName))

    def installRolesAndGroups(self):
        '''Registers roles used by workflows and classes defined in this
           application if they are not registered yet. Creates the corresponding
           groups if needed.'''
        site = self.ploneSite
        data = list(site.__ac_roles__)
        for role in self.config.applicationRoles:
            if not role in data:
                data.append(role)
                # Add to portal_role_manager
                # First, try to fetch it. If it's not there, we probaly have no
                # PAS or another way to deal with roles was configured.
                try:
                    prm = site.acl_users.get('portal_role_manager', None)
                    if prm is not None:
                        try:
                            prm.addRole(role, role,
                                "Added by product '%s'" % self.productName)
                        except KeyError: # Role already exists
                            pass
                except AttributeError:
                    pass
            # If it is a global role, create a specific group and grant him
            # this role
            if role not in self.config.applicationGlobalRoles: continue
            group = '%s_group' % role
            if site.portal_groups.getGroupById(group): continue # Already there
            site.portal_groups.addGroup(group, title=group)
            site.portal_groups.setRolesForGroup(group, [role])
        site.__ac_roles__ = tuple(data)

    def installWorkflows(self):
        '''Creates or updates the workflows defined in the application.'''
        wfTool = self.ploneSite.portal_workflow
        for contentType, workflowName in self.workflows.iteritems():
            # Register the workflow if needed
            if workflowName not in wfTool.listWorkflows():
                wfMethod = self.config.ExternalMethod('temp', 'temp',
                    self.productName + '.workflows', 'create_%s' % workflowName)
                workflow = wfMethod(self, workflowName)
                wfTool._setObject(workflowName, workflow)
            else:
                self.appyTool.log('%s already in workflows.' % workflowName)
            # Link the workflow to the current content type
            wfTool.setChainForPortalTypes([contentType], workflowName)
        return wfTool

    def installStyleSheet(self):
        '''Registers In Plone the stylesheet linked to this application.'''
        cssName = self.productName + '.css'
        cssTitle = self.productName + ' CSS styles'
        cssInfo = {'id': cssName, 'title': cssTitle}
        portalCss = self.ploneSite.portal_css
        try:
            portalCss.unregisterResource(cssInfo['id'])
        except:
            pass
        defaults = {'id': '', 'media': 'all', 'enabled': True}
        defaults.update(cssInfo)
        portalCss.registerStylesheet(**defaults)

    def managePortlets(self):
        '''Shows or hides the application-specific portlet and configures other
           Plone portlets if relevant.'''
        portletName= 'here/%s_portlet/macros/portlet' % self.productName.lower()
        site = self.ploneSite
        leftPortlets = site.getProperty('left_slots')
        if not leftPortlets: leftPortlets = []
        else: leftPortlets = list(leftPortlets)
        
        if self.showPortlet and (portletName not in leftPortlets):
            leftPortlets.insert(0, portletName)
        if not self.showPortlet and (portletName in leftPortlets):
            leftPortlets.remove(portletName)
        # Remove some basic Plone portlets that make less sense when building
        # web applications.
        portletsToRemove = ["here/portlet_navigation/macros/portlet",
                            "here/portlet_recent/macros/portlet",
                            "here/portlet_related/macros/portlet"]
        if not self.minimalistPlone: portletsToRemove = []
        for p in portletsToRemove:
            if p in leftPortlets:
                leftPortlets.remove(p)
        site.manage_changeProperties(left_slots=tuple(leftPortlets))
        if self.minimalistPlone:
            site.manage_changeProperties(right_slots=())

    def manageIndexes(self):
        '''For every indexed field, this method installs and updates the
           corresponding index if it does not exist yet.'''
        indexInfo = {}
        for className, appyTypes in self.attributes.iteritems():
            for appyType in appyTypes:
                if not appyType.indexed or (appyType.name == 'title'): continue
                n = appyType.name
                indexName = 'get%s%s' % (n[0].upper(), n[1:])
                indexInfo[indexName] = appyType.getIndexType()
        if indexInfo:
            PloneInstaller.updateIndexes(self.ploneSite, indexInfo, self)

    def manageLanguages(self):
        '''Manages the languages supported by the application.'''
        if self.languageSelector:
            # We must install the PloneLanguageTool if not done yet
            qi = self.ploneSite.portal_quickinstaller
            if not qi.isProductInstalled('PloneLanguageTool'):
                qi.installProduct('PloneLanguageTool')
            languageTool = self.ploneSite.portal_languages
            defLanguage = self.languages[0]
            languageTool.manage_setLanguageSettings(defaultLanguage=defLanguage,
                supportedLanguages=self.languages, setContentN=None,
                setCookieN=True, setRequestN=True, setPathN=True,
                setForcelanguageUrls=True, setAllowContentLanguageFallback=None,
                setUseCombinedLanguageCodes=None, displayFlags=False,
                startNeutral=False)

    def finalizeInstallation(self):
        '''Performs some final installation steps.'''
        site = self.ploneSite
        # Do not generate an action (tab) for each root folder
        if self.minimalistPlone:
            site.portal_properties.site_properties.manage_changeProperties(
                disable_folder_sections=True)
        # Do not allow an anonymous user to register himself as new user
        site.manage_permission('Add portal member', ('Manager',), acquire=0)
        # Call custom installer if any
        if hasattr(self.appyTool, 'install'):
            self.tool.executeAppyAction('install', reindex=False)
        # Patch the "logout" action with a custom Appy one that updates the
        # list of currently logged users.
        for action in site.portal_membership._actions:
            if action.id == 'logout':
                action.setActionExpression(
                    'string:${portal_url}/%s/logout' % self.toolInstanceName)
        # Replace Plone front-page with an application-specific page if needed
        if self.appFrontPage:
            frontPageName = self.productName + 'FrontPage'
            site.manage_changeProperties(default_page=frontPageName)

    def info(self, msg): return self.appyTool.log(msg)

    def install(self):
        if self.minimalistPlone: self.customizePlone()
        self.installRootFolder()
        self.installTypes()
        self.installTool()
        self.installTranslations()
        self.installRolesAndGroups()
        self.installWorkflows()
        self.installStyleSheet()
        self.managePortlets()
        self.manageIndexes()
        self.manageLanguages()
        self.finalizeInstallation()
        self.appyTool.log("Installation done.")

    def uninstallTool(self):
        site = self.ploneSite
        # Unmention tool in the search form
        portalProperties = getattr(site, 'portal_properties', None)
        if portalProperties is not None:
            siteProperties = getattr(portalProperties, 'site_properties', None)
            if siteProperties is not None and \
               siteProperties.hasProperty('types_not_searched'):
                current = list(siteProperties.getProperty('types_not_searched'))
                if self.toolName in current:
                    current.remove(self.toolName)
                    siteProperties.manage_changeProperties(
                        **{'types_not_searched' : current})

        # Unmention tool in the navigation
        if portalProperties is not None:
            nvProps = getattr(portalProperties, 'navtree_properties', None)
            if nvProps is not None and nvProps.hasProperty('idsNotToList'):
                current = list(nvProps.getProperty('idsNotToList'))
                if self.toolInstanceName in current:
                    current.remove(self.toolInstanceName)
                    nvProps.manage_changeProperties(**{'idsNotToList': current})

    def uninstall(self):
        self.uninstallTool()
        return 'Done.'

# Stuff for tracking user activity ---------------------------------------------
loggedUsers = {}
originalTraverse = None
doNotTrack = ('.jpg','.gif','.png','.js','.class','.css')

def traverseWrapper(self, path, response=None, validated_hook=None):
    '''This function is called every time a users gets a URL, this is used for
       tracking user activity. self is a BaseRequest'''
    res = originalTraverse(self, path, response, validated_hook)
    t = time.time()
    if os.path.splitext(path)[-1].lower() not in doNotTrack:
        # Do nothing when the user gets non-pages
        userId = self['AUTHENTICATED_USER'].getId()
        if userId: loggedUsers[userId] = t
    return res

# ------------------------------------------------------------------------------
class ZopeInstaller:
    '''This Zope installer runs every time Zope starts and encounters this
       generated Zope product.'''
    def __init__(self, zopeContext, toolClass, config, classes):
        self.zopeContext = zopeContext
        self.toolClass = toolClass
        self.config = cfg = config
        self.classes = classes
        # Unwrap some useful config variables
        self.productName = cfg.PROJECTNAME
        self.logger = cfg.logger
        self.defaultAddContentPermission = cfg.DEFAULT_ADD_CONTENT_PERMISSION
        self.addContentPermissions = cfg.ADD_CONTENT_PERMISSIONS

    def completeAppyTypes(self):
        '''We complete here the initialisation process of every Appy type of
           every gen-class of the application.'''
        for klass in self.classes:
            for baseClass in klass.wrapperClass.__bases__:
                for name, appyType in baseClass.__dict__.iteritems():
                    if isinstance(appyType, Type):
                        appyType.init(name, baseClass, self.productName)
                    # Do not forget back references
                    if isinstance(appyType, Ref):
                        bAppyType = appyType.back
                        bAppyType.init(bAppyType.attribute, appyType.klass,
                                       self.productName)
                        bAppyType.klass = baseClass

    def installApplication(self):
        '''Performs some application-wide installation steps.'''
        register = self.config.DirectoryView.registerDirectory
        register('skins', self.config.__dict__)
        # Register the appy skin folder among DirectoryView'able folders
        register('skin', appy.getPath() + '/gen/plone25')

    def installTool(self):
        '''Installs the tool.'''
        self.config.ToolInit(self.productName + ' Tools',
            tools = [self.toolClass], icon='tool.gif').initialize(
                self.zopeContext)

    def installTypes(self):
        '''Installs and configures the types defined in the application.'''
        contentTypes, constructors, ftis = self.config.process_types(
            self.config.listTypes(self.productName), self.productName)

        self.config.cmfutils.ContentInit(self.productName + ' Content',
            content_types = contentTypes,
            permission = self.defaultAddContentPermission,
            extra_constructors = constructors, fti = ftis).initialize(
                self.zopeContext)

        # Define content-specific "add" permissions
        for i in range(0, len(contentTypes)):
            className = contentTypes[i].__name__
            if not className in self.addContentPermissions: continue
            self.zopeContext.registerClass(meta_type = ftis[i]['meta_type'],
                constructors = (constructors[i],),
                permission = self.addContentPermissions[className])

    def enableUserTracking(self):
        '''Enables the machinery allowing to know who is currently logged in.
           Information about logged users will be stored in RAM, in the variable
           named loggedUsers defined above.'''
        global originalTraverse
        if not originalTraverse:
            # User tracking is not enabled yet. Do it now.
            BaseRequest = self.config.BaseRequest
            originalTraverse = BaseRequest.traverse
            BaseRequest.traverse = traverseWrapper

    def finalizeInstallation(self):
        '''Performs some final installation steps.'''
        # Apply customization policy if any
        cp = self.config.CustomizationPolicy
        if cp and hasattr(cp, 'register'): cp.register(context)

    def install(self):
        self.logger.info('is being installed...')
        self.completeAppyTypes()
        self.installApplication()
        self.installTool()
        self.installTypes()
        self.enableUserTracking()
        self.finalizeInstallation()
# ------------------------------------------------------------------------------
